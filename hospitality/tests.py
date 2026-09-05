from django.test import TestCase

from authentication.models import User
from camps.models import Camp, Faena
from companies.models import Client, Company
from garments.models import GarmentType
from hospitality.models import BatchStatus
from hospitality.schemas import LinenBatchIn, LinenBatchItemIn, ReturnCountIn
from hospitality.services import (
	build_batch_summary,
	create_batch,
	dispatch_batch,
	register_return_count,
	start_processing,
)


class HospitalityFlowTests(TestCase):
	def setUp(self):
		self.operator = User.objects.create_user(
			username='hospitality_operator', password='test', role=User.Role.SUPERVISOR
		)
		faena = Faena.objects.create(name='Faena hotelería')
		self.camp = Camp.objects.create(faena=faena, name='Campamento hotelería')
		client = Client.objects.create(name='Cliente hotelería', faena=faena, reference_prefix='H')
		self.company = Company.objects.create(
			client=client,
			name='Empresa hotelería',
			service_type=Company.ServiceType.HOSPITALITY,
			delivery_flow=Company.DeliveryFlow.CLIENT_ONLY,
		)
		self.sheet = GarmentType.objects.create(code='SAB', name='Sábana')
		self.towel = GarmentType.objects.create(code='TOA', name='Toalla')

	def test_complete_batch_flow_records_shortage_and_recipient(self):
		batch = create_batch(
			LinenBatchIn(
				company_id=self.company.id,
				camp_id=self.camp.id,
				weight_kg=286.5,
				items=[
					LinenBatchItemIn(garment_type_id=self.sheet.id, quantity_in=120),
					LinenBatchItemIn(garment_type_id=self.towel.id, quantity_in=80),
				],
			),
			self.operator,
		)
		self.assertEqual(batch.status, BatchStatus.RECEIVED)

		batch = start_processing(batch.id, self.operator)
		self.assertEqual(batch.status, BatchStatus.IN_PROCESS)

		items = {item.garment_type_id: item for item in batch.items.all()}
		batch = register_return_count(
			batch.id,
			[
				ReturnCountIn(item_id=items[self.sheet.id].id, quantity_out=118),
				ReturnCountIn(item_id=items[self.towel.id].id, quantity_out=79),
			],
			self.operator,
		)
		self.assertEqual(
			build_batch_summary(batch),
			{'total_in': 200, 'total_out': 197, 'shortage': 3, 'is_counted': True},
		)

		batch = dispatch_batch(
			batch.id, self.operator, received_by_client='Marcela Soto', note='Conteo validado.'
		)
		self.assertEqual(batch.status, BatchStatus.DISPATCHED)
		self.assertIsNotNone(batch.dispatched_at)
		self.assertEqual(batch.received_by_client, 'Marcela Soto')
		self.assertIn('Conteo validado.', batch.observations)
