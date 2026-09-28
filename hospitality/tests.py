from datetime import timedelta
from uuid import uuid4

from django.test import Client as HttpClient, TestCase, override_settings
from django.utils import timezone

from authentication.models import User
from authentication.services import issue_tokens
from camps.models import Camp, Faena
from companies.models import Client, Company
from garments.models import GarmentType
from hospitality.models import LinenMovement
from hospitality.schemas import CountLineIn, FieldMovementIn, LinenLineIn
from hospitality import services


class LinenStockTests(TestCase):
    def setUp(self):
        self.admin = User.objects.create_user(username='hotel-admin', password='x', role=User.Role.ADMIN)
        self.packer = User.objects.create_user(
            username='hotel-empaque', password='x', role=User.Role.DIGITADOR_EMPAQUE
        )
        self.supervisor = User.objects.create_user(
            username='hotel-supervisor', password='x', role=User.Role.SUPERVISOR
        )
        faena = Faena.objects.create(name='Faena hotelería')
        self.central = Camp.objects.create(faena=faena, name='Campamento Central')
        self.north = Camp.objects.create(faena=faena, name='Campamento Norte')
        other_faena = Faena.objects.create(name='Otra faena')
        self.foreign_camp = Camp.objects.create(faena=other_faena, name='Campamento ajeno')

        client = Client.objects.create(name='Cliente hotelería', faena=faena, reference_prefix='H')
        self.company = Company.objects.create(
            client=client,
            name='Hotel Cordillera',
            service_type=Company.ServiceType.HOSPITALITY,
            delivery_flow=Company.DeliveryFlow.CLIENT_ONLY,
        )
        self.sheet = GarmentType.objects.create(code='SAB', name='Sábana', is_linen=True)
        self.towel = GarmentType.objects.create(code='TOA', name='Toalla', is_linen=True)
        self.shirt = GarmentType.objects.create(code='CAM', name='Camisa de trabajo')

    # --- helpers ---

    def balance(self, location_name: str, garment_type: GarmentType) -> int:
        rows = services.compute_balances(self.company)['locations']
        row = next(row for row in rows if row['name'] == location_name)
        return next(line['quantity'] for line in row['lines'] if line['garment_type_id'] == garment_type.id)

    def field(self, kind, camp, lines, occurred_at=None, client_uuid=None):
        return services.sync_field_movement(
            FieldMovementIn(
                client_uuid=client_uuid or uuid4(),
                kind=kind,
                company_id=self.company.id,
                camp_id=camp.id,
                lines=[LinenLineIn(garment_type_id=gt.id, quantity=qty) for gt, qty in lines],
                occurred_at=occurred_at or timezone.now(),
                latitude=-23.65,
                longitude=-70.4,
                accuracy_meters=5,
            ),
            self.supervisor,
        )

    def count(self, camp, lines):
        return services.register_count(
            self.company.id,
            camp.id if camp else None,
            [CountLineIn(garment_type_id=gt.id, counted=qty) for gt, qty in lines],
            self.admin,
        )

    # --- tests ---

    def test_flows_move_linen_between_locations(self):
        dispatch = services.register_dispatch(
            self.company.id, [LinenLineIn(garment_type_id=self.sheet.id, quantity=50)], self.packer
        )
        self.assertEqual(dispatch.number, f'HD-{timezone.localtime().year}-0001')

        self.field('REPARTO', self.central, [(self.sheet, 30)])
        self.field('RETIRO', self.central, [(self.sheet, 10)])

        self.assertEqual(self.balance('Por repartir en faena', self.sheet), 20)
        self.assertEqual(self.balance('Campamento Central', self.sheet), 20)
        # Retirado sucio (10) menos despachado limpio (50).
        self.assertEqual(self.balance('En poder de Servilion', self.sheet), -40)

    def test_count_sets_the_balance_and_records_the_difference(self):
        self.field('REPARTO', self.central, [(self.sheet, 30)])
        count = self.count(self.central, [(self.sheet, 25)])

        self.assertEqual(self.balance('Campamento Central', self.sheet), 25)
        self.assertEqual(count.lines.get().difference, -5)

        self.field('RETIRO', self.central, [(self.sheet, 5)])
        self.assertEqual(self.balance('Campamento Central', self.sheet), 20)

    def test_offline_movement_older_than_a_count_is_already_inside_it(self):
        self.count(self.central, [(self.sheet, 100)])
        # Retiro registrado sin señal ANTES del conteo, que sincroniza después:
        # quien contó ya no vio esas sábanas, así que no se descuentan de nuevo.
        self.field('RETIRO', self.central, [(self.sheet, 10)], occurred_at=timezone.now() - timedelta(hours=2))

        self.assertEqual(self.balance('Campamento Central', self.sheet), 100)
        # Pero en Servilion sí entran: ahí no se contó nada.
        self.assertEqual(self.balance('En poder de Servilion', self.sheet), 10)

    def test_count_only_touches_the_types_it_brings(self):
        self.count(self.central, [(self.sheet, 40), (self.towel, 30)])
        self.count(self.central, [(self.sheet, 35)])
        self.assertEqual(self.balance('Campamento Central', self.towel), 30)

    def test_collecting_more_than_the_balance_is_allowed_and_flagged(self):
        self.count(self.north, [(self.towel, 10)])
        result = self.field('RETIRO', self.north, [(self.towel, 15)])

        self.assertEqual(result['status'], 'CREADO')
        rows = {row['name']: row for row in services.compute_balances(self.company)['locations']}
        self.assertEqual(self.balance('Campamento Norte', self.towel), -5)
        self.assertTrue(rows['Campamento Norte']['has_negative'])

    def test_field_sync_is_idempotent(self):
        client_uuid = uuid4()
        first = self.field('REPARTO', self.central, [(self.sheet, 5)], client_uuid=client_uuid)
        second = self.field('REPARTO', self.central, [(self.sheet, 5)], client_uuid=client_uuid)

        self.assertEqual(first['status'], 'CREADO')
        self.assertEqual(second['status'], 'DUPLICADO')
        self.assertEqual(second['movement_id'], first['movement_id'])
        self.assertEqual(self.balance('Campamento Central', self.sheet), 5)

    def test_field_sync_rejects_foreign_camp_and_non_linen_types(self):
        foreign = self.field('REPARTO', self.foreign_camp, [(self.sheet, 5)])
        not_linen = self.field('REPARTO', self.central, [(self.shirt, 5)])

        self.assertEqual(foreign['status'], 'ERROR')
        self.assertEqual(not_linen['status'], 'ERROR')
        self.assertIn('Camisa de trabajo', not_linen['detail'])
        self.assertFalse(LinenMovement.objects.exists())

    def test_voided_movement_leaves_the_balance(self):
        result = self.field('REPARTO', self.central, [(self.sheet, 12)])
        services.void_movement(result['movement_id'], self.admin, 'Registrado dos veces.')

        self.assertEqual(self.balance('Campamento Central', self.sheet), 0)
        with self.assertRaises(services.LinenFlowError):
            services.void_movement(result['movement_id'], self.admin, 'Otra vez.')

    @override_settings(ALLOW_PLANT_OPERATIONS=False)
    def test_cloud_refuses_plant_dispatch(self):
        # El despacho sale de la planta (servidor local); la nube no emite `HD-`.
        body = {'company_id': self.company.id, 'lines': [{'garment_type_id': self.sheet.id, 'quantity': 3}]}
        token = issue_tokens(self.packer)['access']
        response = HttpClient().post('/api/hospitality/dispatches', body, content_type='application/json',
                                     HTTP_AUTHORIZATION=f'Bearer {token}')
        self.assertEqual(response.status_code, 409)
        self.assertFalse(LinenMovement.objects.exists())

    @override_settings(ALLOW_PLANT_OPERATIONS=True)
    def test_roles_on_the_api(self):
        http = HttpClient()

        def auth(user):
            return {'HTTP_AUTHORIZATION': f"Bearer {issue_tokens(user)['access']}"}

        dispatch_body = {'company_id': self.company.id, 'lines': [{'garment_type_id': self.sheet.id, 'quantity': 3}]}
        response = http.post('/api/hospitality/dispatches', dispatch_body, content_type='application/json',
                             **auth(self.supervisor))
        self.assertEqual(response.status_code, 403)
        response = http.post('/api/hospitality/dispatches', dispatch_body, content_type='application/json',
                             **auth(self.packer))
        self.assertEqual(response.status_code, 201)

        count_body = {'company_id': self.company.id, 'camp_id': self.central.id,
                      'lines': [{'garment_type_id': self.sheet.id, 'counted': 7}]}
        response = http.post('/api/hospitality/counts', count_body, content_type='application/json',
                             **auth(self.supervisor))
        self.assertEqual(response.status_code, 403)
        response = http.post('/api/hospitality/counts', count_body, content_type='application/json',
                             **auth(self.admin))
        self.assertEqual(response.status_code, 201)

        response = http.get('/api/hospitality/balances', **auth(self.packer))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()[0]['company_name'], 'Hotel Cordillera')
