from io import StringIO

from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import TestCase

from authentication.models import User
from camps.models import Camp, Faena, Room
from companies.models import Client, ClientGarmentPrice, Company
from garments.models import GarmentType
from hospitality.models import BatchStatus, LinenBatch
from orders.models import LaundryOrder, OrderStatus, ReferenceCounter, SiteScan, SyncConflict
from weighing.models import WeighIn
from workers.models import Worker


class SeedDemoCommandTests(TestCase):
    def setUp(self):
        self.access_user = User.objects.create_user(
            username='operador_existente', password='Clave.2026', role=User.Role.ADMIN
        )
        self.user_snapshot = list(
            User.objects.order_by('id').values_list('id', 'username', 'password', 'role')
        )
        old_client = Client.objects.create(name='Cliente que debe desaparecer')
        Company.objects.create(client=old_client, name='Empresa que debe desaparecer')

    def test_requires_explicit_confirmation(self):
        with self.assertRaisesMessage(CommandError, '--confirm'):
            call_command('seed_demo', stdout=StringIO())

        self.assertTrue(Client.objects.filter(name='Cliente que debe desaparecer').exists())

    def test_replaces_business_data_and_preserves_access_users(self):
        call_command('seed_demo', confirm=True, stdout=StringIO())

        self.assertEqual(
            list(User.objects.order_by('id').values_list('id', 'username', 'password', 'role')),
            self.user_snapshot,
        )
        self.assertTrue(self.access_user.check_password('Clave.2026'))
        self.assertFalse(Client.objects.filter(name='Cliente que debe desaparecer').exists())

        self.assertEqual(Faena.objects.count(), 1)
        self.assertEqual(Camp.objects.count(), 2)
        self.assertEqual(Room.objects.count(), 6)
        self.assertEqual(Client.objects.count(), 3)
        self.assertEqual(Company.objects.count(), 4)
        self.assertEqual(Worker.objects.count(), 7)
        self.assertEqual(GarmentType.objects.count(), 6)
        self.assertEqual(ClientGarmentPrice.objects.count(), 12)
        self.assertEqual(LaundryOrder.objects.count(), 10)
        self.assertEqual(WeighIn.objects.count(), 2)
        self.assertEqual(LinenBatch.objects.count(), 3)

        for status in OrderStatus.values:
            self.assertTrue(
                LaundryOrder.objects.filter(status=status).exists(),
                f'El estado {status} no está representado en la demo.',
            )
        for status in BatchStatus.values:
            self.assertTrue(LinenBatch.objects.filter(status=status).exists())

        self.assertEqual(SiteScan.objects.filter(kind=SiteScan.Kind.DELIVERY).count(), 2)
        self.assertEqual(
            SiteScan.objects.filter(kind=SiteScan.Kind.DELIVERY, latitude__isnull=False).count(),
            2,
        )
        self.assertTrue(
            LaundryOrder.objects.filter(
                company__delivery_flow=Company.DeliveryFlow.WITH_ROOM_DELIVERY,
                status=OrderStatus.DELIVERED,
                delivery_room__isnull=False,
            ).exists()
        )
        self.assertTrue(
            LaundryOrder.objects.filter(
                company__delivery_flow=Company.DeliveryFlow.CLIENT_ONLY,
                status=OrderStatus.DELIVERED,
                delivery_room__isnull=True,
            ).exists()
        )
        self.assertEqual(SyncConflict.objects.count(), 2)
        self.assertEqual(ReferenceCounter.objects.get(prefix='M').last_number, 1008)