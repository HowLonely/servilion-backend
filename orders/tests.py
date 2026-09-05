from uuid import uuid4

from django.test import Client as HttpClient, TransactionTestCase
from django.utils import timezone

from authentication.models import User
from authentication.services import issue_tokens
from camps.models import Camp, Faena, Room
from companies.models import Client, Company
from orders.models import LaundryOrder, OrderStatus, SiteScan
from workers.models import Worker


class MobileDeliveryTests(TransactionTestCase):
    def setUp(self):
        self.http = HttpClient()
        self.user = User.objects.create_user(
            username='delivery-supervisor', password='test', role=User.Role.SUPERVISOR
        )
        self.auth = {'HTTP_AUTHORIZATION': f"Bearer {issue_tokens(self.user)['access']}"}
        self.client = Client.objects.create(name='Delivery Test Client')
        self.faena = Faena.objects.create(name='Delivery Test Site')
        self.camp = Camp.objects.create(faena=self.faena, name='Camp A')
        self.expected_room = Room.objects.create(camp=self.camp, number='101')
        self.other_room = Room.objects.create(camp=self.camp, number='202')

    def make_order(self, code: str, flow: str) -> LaundryOrder:
        company = Company.objects.create(
            client=self.client,
            name=f'Company {code}',
            delivery_flow=flow,
        )
        worker = Worker.objects.create(
            company=company,
            badge_code=f'W-{code}',
            full_name=f'Worker {code}',
            current_room=self.expected_room,
        )
        order = LaundryOrder.objects.create(
            order_number=code,
            worker=worker,
            company=company,
            status=OrderStatus.DISPATCHED,
            received_at=timezone.now(),
            completed_at=timezone.now(),
            dispatched_at=timezone.now(),
            reference=code,
            delivery_room=self.expected_room if flow == Company.DeliveryFlow.WITH_ROOM_DELIVERY else None,
        )
        if flow == Company.DeliveryFlow.WITH_ROOM_DELIVERY:
            SiteScan.objects.create(
                kind=SiteScan.Kind.CLEAN_IN,
                scanned_code=code,
                order=order,
                scanned_at=timezone.now(),
            )
        return order

    def post_delivery(self, **overrides):
        payload = {
            'client_uuid': str(uuid4()),
            'latitude': -24.625,
            'longitude': -70.402,
            'accuracy_meters': 8.5,
            **overrides,
        }
        return self.http.post('/api/delivery/confirm', payload, content_type='application/json', **self.auth)

    def test_flow_one_saves_room_location_and_is_idempotent(self):
        order = self.make_order('TEST-F1', Company.DeliveryFlow.WITH_ROOM_DELIVERY)
        event_id = str(uuid4())
        payload = {
            'client_uuid': event_id,
            'order_code': 'TEST-F1',
            'room_qr': str(self.expected_room.qr_code),
        }

        response = self.post_delivery(**payload)
        self.assertEqual(response.status_code, 200, response.content)
        retry = self.post_delivery(**payload)
        self.assertEqual(retry.status_code, 200, retry.content)

        order.refresh_from_db()
        scan = SiteScan.objects.get(order=order, kind=SiteScan.Kind.DELIVERY)
        self.assertEqual(order.status, OrderStatus.DELIVERED)
        self.assertEqual(scan.room, self.expected_room)
        self.assertEqual(str(scan.client_uuid), event_id)
        self.assertIsNotNone(scan.latitude)
        self.assertEqual(SiteScan.objects.filter(order=order, kind=SiteScan.Kind.DELIVERY).count(), 1)

    def test_flow_one_requires_second_confirmation_for_different_room(self):
        order = self.make_order('TEST-MISMATCH', Company.DeliveryFlow.WITH_ROOM_DELIVERY)
        payload = {
            'order_code': 'TEST-MISMATCH',
            'room_qr': str(self.other_room.qr_code),
        }

        warning = self.post_delivery(**payload)
        self.assertEqual(warning.status_code, 409, warning.content)
        confirmed = self.post_delivery(**payload, confirm_different_room=True)
        self.assertEqual(confirmed.status_code, 200, confirmed.content)

        order.refresh_from_db()
        self.assertEqual(order.status, OrderStatus.DELIVERED)
        self.assertFalse(confirmed.json()['room_matched'])

    def test_flow_two_delivers_to_client_without_room(self):
        order = self.make_order('TEST-F2', Company.DeliveryFlow.CLIENT_ONLY)

        response = self.post_delivery(order_code='TEST-F2')
        self.assertEqual(response.status_code, 200, response.content)

        order.refresh_from_db()
        body = response.json()
        scan = SiteScan.objects.get(order=order, kind=SiteScan.Kind.DELIVERY)
        self.assertEqual(body['delivery_target'], 'CLIENTE')
        self.assertIsNone(body['scanned_room'])
        self.assertEqual(order.status, OrderStatus.DELIVERED)
        self.assertIsNone(scan.room)
        self.assertIsNotNone(scan.longitude)

    def test_location_is_required(self):
        self.make_order('TEST-GPS', Company.DeliveryFlow.CLIENT_ONLY)

        response = self.http.post(
            '/api/delivery/confirm',
            {'client_uuid': str(uuid4()), 'order_code': 'TEST-GPS'},
            content_type='application/json',
            **self.auth,
        )

        self.assertEqual(response.status_code, 422)