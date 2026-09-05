"""Reinicia los datos de negocio y carga un escenario integral de demostración.

Las cuentas de acceso de ``authentication.User`` nunca se eliminan ni se
modifican. El comando exige ``--confirm`` porque borra datos operativos reales.
"""

from datetime import timedelta
from decimal import Decimal
from uuid import NAMESPACE_URL, uuid5

from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.utils import timezone

from authentication.models import User
from camps.models import Camp, Faena, Room
from companies.models import Client, ClientGarmentPrice, Company
from garments.models import GarmentType
from hospitality.models import BatchStatus, LinenBatch, LinenBatchItem
from orders.models import (
    LaundryOrder,
    MissingItemResolution,
    OrderItem,
    OrderStatus,
    OrderStatusHistory,
    ReferenceCounter,
    SiteScan,
    SyncConflict,
)
from weighing.models import WeighIn, WeighLabel
from workers.models import Worker


def demo_uuid(key: str):
    return uuid5(NAMESPACE_URL, f'https://servilion.cl/demo/{key}')


class Command(BaseCommand):
    help = 'Borra datos de negocio, conserva usuarios y carga datos integrales de demostración.'

    def add_arguments(self, parser):
        parser.add_argument(
            '--confirm',
            action='store_true',
            help='Confirma el borrado irreversible de los datos de negocio actuales.',
        )
        parser.add_argument(
            '--dry-run',
            action='store_true',
            help='Muestra cuántos registros se borrarían sin modificar la base.',
        )

    def handle(self, *args, **options):
        counts = self._business_counts()
        if options['dry_run']:
            self.stdout.write('Datos de negocio que se reemplazarían:')
            for label, count in counts.items():
                self.stdout.write(f'  {label:<22} {count:>8}')
            self.stdout.write(f'  {"Usuarios preservados":<22} {User.objects.count():>8}')
            return

        if not options['confirm']:
            raise CommandError(
                'Este comando elimina todos los datos de negocio. '
                'Ejecuta nuevamente con --confirm o usa --dry-run.'
            )

        with transaction.atomic():
            user_snapshot = set(User.objects.values_list('id', flat=True))
            self._clear_business_data()
            summary = self._create_demo_data()
            if set(User.objects.values_list('id', flat=True)) != user_snapshot:
                raise CommandError('La lista de usuarios cambió durante el reset; se revirtió la transacción.')

        self.stdout.write(self.style.SUCCESS('Datos de negocio reemplazados por el escenario demo.'))
        for label, count in summary.items():
            self.stdout.write(f'  {label:<22} {count:>8}')
        self.stdout.write(f'  {"Usuarios preservados":<22} {User.objects.count():>8}')

    @staticmethod
    def _business_counts() -> dict[str, int]:
        return {
            'Órdenes': LaundryOrder.objects.count(),
            'Pesajes': WeighIn.objects.count(),
            'Lotes hotelería': LinenBatch.objects.count(),
            'Trabajadores': Worker.objects.count(),
            'Empresas': Company.objects.count(),
            'Clientes': Client.objects.count(),
            'Habitaciones': Room.objects.count(),
            'Campamentos': Camp.objects.count(),
            'Faenas': Faena.objects.count(),
            'Tipos de prenda': GarmentType.objects.count(),
        }

    @staticmethod
    def _clear_business_data() -> None:
        LaundryOrder.objects.all().delete()
        WeighIn.objects.all().delete()
        LinenBatch.objects.all().delete()
        Worker.objects.all().delete()
        ClientGarmentPrice.objects.all().delete()
        Company.objects.all().delete()
        Client.objects.all().delete()
        Room.objects.all().delete()
        Camp.objects.all().delete()
        Faena.objects.all().delete()
        GarmentType.objects.all().delete()
        ReferenceCounter.objects.all().delete()

    def _create_demo_data(self) -> dict[str, int]:
        now = timezone.now()
        users = self._operators()

        faena = Faena.objects.create(name='Faena Horizonte')
        camps = {
            'norte': Camp.objects.create(faena=faena, name='Campamento Norte'),
            'central': Camp.objects.create(faena=faena, name='Campamento Central'),
        }
        rooms = {}
        for camp_key, numbers in {'norte': ('101', '102', '103'), 'central': ('201', '202', '203')}.items():
            for number in numbers:
                key = f'{camp_key}-{number}'
                rooms[key] = Room.objects.create(
                    camp=camps[camp_key],
                    number=number,
                    qr_code=demo_uuid(f'room/{key}'),
                )

        clients = {
            'room': Client.objects.create(
                name='Minera Horizonte', tax_id='76.111.111-1', faena=faena,
                reference_prefix='M', contact_name='Carolina Rojas', phone='+56 9 6111 1111',
            ),
            'direct': Client.objects.create(
                name='Operaciones Salar', tax_id='76.222.222-2', faena=faena,
                reference_prefix='S', contact_name='Felipe Díaz', phone='+56 9 6222 2222',
                is_single_company=True,
            ),
            'hotel': Client.objects.create(
                name='Hotel Cordillera', tax_id='76.333.333-3', faena=faena,
                reference_prefix='C', contact_name='Paula Soto', phone='+56 9 6333 3333',
                is_single_company=True,
            ),
        }
        companies = {
            'principal': Company.objects.create(
                client=clients['room'], name='Minera Horizonte', tax_id='76.111.111-1',
                client_role=Company.ClientRole.PRINCIPAL,
                delivery_flow=Company.DeliveryFlow.WITH_ROOM_DELIVERY,
            ),
            'contractor': Company.objects.create(
                client=clients['room'], name='Andes Servicios', tax_id='76.444.444-4',
                client_role=Company.ClientRole.CONTRACTOR,
                delivery_flow=Company.DeliveryFlow.WITH_ROOM_DELIVERY,
            ),
            'direct': Company.objects.create(
                client=clients['direct'], name='Operaciones Salar', tax_id='76.222.222-2',
                client_role=Company.ClientRole.PRINCIPAL,
                delivery_flow=Company.DeliveryFlow.CLIENT_ONLY,
            ),
            'hotel': Company.objects.create(
                client=clients['hotel'], name='Hotel Cordillera', tax_id='76.333.333-3',
                client_role=Company.ClientRole.PRINCIPAL,
                service_type=Company.ServiceType.HOSPITALITY,
                delivery_flow=Company.DeliveryFlow.CLIENT_ONLY,
            ),
        }

        garments = {}
        for code, name in (
            ('CAM', 'Camisa de trabajo'),
            ('PAN', 'Pantalón de trabajo'),
            ('OVE', 'Overol'),
            ('CHA', 'Chaqueta térmica'),
            ('TOA', 'Toalla'),
            ('SAB', 'Sábana'),
        ):
            garments[code] = GarmentType.objects.create(code=code, name=name)

        price_maps = {
            'room': {'CAM': '4200', 'PAN': '4800', 'OVE': '6900', 'CHA': '7500', 'TOA': '2100'},
            'direct': {'CAM': '3900', 'PAN': '4500', 'OVE': '6500', 'CHA': '7100', 'TOA': '1900'},
            'hotel': {'TOA': '1350', 'SAB': '1800'},
        }
        for client_key, prices in price_maps.items():
            ClientGarmentPrice.objects.bulk_create(
                ClientGarmentPrice(
                    client=clients[client_key], garment_type=garments[code], unit_price=Decimal(price)
                )
                for code, price in prices.items()
            )

        workers = {
            'ana': self._worker(companies['principal'], 'MH-001', 'Ana Morales', '11.111.111-1', rooms['norte-101'], '7x7', 'Operadora'),
            'diego': self._worker(companies['principal'], 'MH-002', 'Diego Araya', '12.222.222-2', rooms['norte-102'], '14x14', 'Mecánico'),
            'valentina': self._worker(companies['principal'], 'MH-003', 'Valentina Pérez', '13.333.333-3', rooms['central-201'], '7x7', 'Prevencionista'),
            'matias': self._worker(companies['contractor'], 'AS-001', 'Matías González', '14.444.444-4', rooms['central-202'], '14x14', 'Técnico'),
            'camila': self._worker(companies['contractor'], 'AS-002', 'Camila Tapia', '15.555.555-5', rooms['norte-103'], '7x7', 'Supervisora'),
            'nicolas': self._worker(companies['direct'], 'OS-001', 'Nicolás Silva', '16.666.666-6', None, '7x7', 'Operador'),
            'fernanda': self._worker(companies['direct'], 'OS-002', 'Fernanda Muñoz', '17.777.777-7', None, '14x14', 'Geóloga'),
        }

        order_specs = [
            ('DEMO-1001', 'M1001A', workers['ana'], OrderStatus.RECEIVED, 0, [('CAM', 2), ('PAN', 2)]),
            ('DEMO-1002', 'M1002A', workers['diego'], OrderStatus.QUALITY_CHECK, 1, [('OVE', 1), ('CAM', 2)]),
            ('DEMO-1003', 'M1003A', workers['valentina'], OrderStatus.INCOMPLETE, 2, [('CAM', 2), ('PAN', 2)]),
            ('DEMO-1004', 'M1004A', workers['matias'], OrderStatus.COMPLETED, 3, [('OVE', 2), ('CHA', 1)]),
            ('DEMO-1005', 'M1005A', workers['camila'], OrderStatus.DISPATCHED, 4, [('CAM', 2), ('PAN', 2), ('TOA', 1)]),
            ('DEMO-1006', 'M1006A', workers['ana'], OrderStatus.DELIVERED, 5, [('CAM', 1), ('PAN', 1)]),
            ('DEMO-1007', 'M1007A', workers['diego'], OrderStatus.DISPATCHED, 1, [('OVE', 1), ('TOA', 2)]),
            ('DEMO-2001', 'S1001A', workers['nicolas'], OrderStatus.DISPATCHED, 2, [('CAM', 2), ('PAN', 1)]),
            ('DEMO-2002', 'S1002A', workers['fernanda'], OrderStatus.DELIVERED, 3, [('OVE', 1), ('CHA', 1)]),
            ('DEMO-2003', 'S1003A', workers['nicolas'], OrderStatus.RECEIVED, 0, [('TOA', 2), ('CAM', 1)]),
        ]
        orders = {}
        for order_number, reference, worker, status, days_ago, items in order_specs:
            orders[order_number] = self._order(
                order_number, reference, worker, status, now - timedelta(days=days_ago, hours=2),
                items, garments, users,
            )

        incomplete_item = orders['DEMO-1003'].items.get(garment_type=garments['PAN'])
        resolution = MissingItemResolution.objects.create(
            order=orders['DEMO-1003'], item=incomplete_item,
            resolution_type=MissingItemResolution.ResolutionType.FOUND,
            quantity=1, resolved_by=users['supervisor'], note='Prenda encontrada en control de calidad.',
        )
        MissingItemResolution.objects.filter(pk=resolution.pk).update(resolved_at=now - timedelta(hours=3))

        for code in ('DEMO-1005', 'DEMO-1007'):
            order = orders[code]
            SiteScan.objects.create(
                kind=SiteScan.Kind.CLEAN_IN, scanned_code=order.reference, order=order,
                scanned_at=order.dispatched_at + timedelta(hours=4), scanned_by=users['supervisor'],
                note='Recepción limpia confirmada en faena.',
            )
        self._delivery_scan(orders['DEMO-1006'], users['supervisor'], rooms['norte-101'], now - timedelta(hours=4))
        self._delivery_scan(orders['DEMO-2002'], users['supervisor'], None, now - timedelta(days=1, hours=2))

        SyncConflict.objects.create(
            order=orders['DEMO-1005'], client_uuid=demo_uuid('conflict/pending'),
            device_updated_at=now - timedelta(hours=6), server_updated_at=now - timedelta(hours=5),
            discarded_payload={'status': 'DESPACHADA', 'source': 'demo'},
        )
        SyncConflict.objects.create(
            order=orders['DEMO-1006'], client_uuid=demo_uuid('conflict/resolved'),
            device_updated_at=now - timedelta(days=1), server_updated_at=now - timedelta(hours=20),
            discarded_payload={'status': 'DESPACHADA', 'source': 'demo'},
            resolved_at=now - timedelta(hours=18), resolved_by=users['admin'],
            resolution_note='Se conservó la entrega confirmada por el servidor.',
        )

        self._create_weigh_ins(now, clients, companies, orders, users)
        self._create_hospitality(now, companies['hotel'], camps, garments, users)
        ReferenceCounter.objects.bulk_create([
            ReferenceCounter(prefix='M', last_number=1008, cycle='A'),
            ReferenceCounter(prefix='S', last_number=1003, cycle='A'),
            ReferenceCounter(prefix='C', last_number=1000, cycle='A'),
        ])
        return self._business_counts()

    @staticmethod
    def _operators() -> dict[str, User | None]:
        def find(role):
            return User.objects.filter(role=role, is_active=True).order_by('id').first()

        fallback = User.objects.filter(is_active=True).order_by('-is_superuser', 'id').first()
        return {
            'admin': find(User.Role.ADMIN) or fallback,
            'supervisor': find(User.Role.SUPERVISOR) or fallback,
            'weighing': find(User.Role.PESAJE) or fallback,
            'digitizer': find(User.Role.DIGITADOR_OT) or fallback,
            'packing': find(User.Role.DIGITADOR_EMPAQUE) or fallback,
        }

    @staticmethod
    def _worker(company, badge, name, national_id, room, shift, position):
        return Worker.objects.create(
            company=company, badge_code=badge, full_name=name, national_id=national_id,
            current_room=room, shift=shift, position=position, area='Operaciones',
        )

    @staticmethod
    def _order(order_number, reference, worker, status, received_at, item_specs, garments, users):
        is_flow_one = worker.company.delivery_flow == Company.DeliveryFlow.WITH_ROOM_DELIVERY
        is_reviewed = status != OrderStatus.RECEIVED
        is_closed = status in (OrderStatus.INCOMPLETE, OrderStatus.COMPLETED, OrderStatus.DISPATCHED, OrderStatus.DELIVERED)
        is_dispatched = status in (OrderStatus.DISPATCHED, OrderStatus.DELIVERED)
        order = LaundryOrder.objects.create(
            client_uuid=demo_uuid(f'order/{order_number}'),
            order_number=order_number,
            ticket_number=f'T-{order_number[-4:]}',
            worker=worker,
            company=worker.company,
            delivery_room=worker.current_room if is_flow_one else None,
            shift=worker.shift,
            status=status,
            garment_count=sum(quantity for _, quantity in item_specs),
            weight_kg=Decimal('8.40'),
            received_at=received_at,
            laundry_received_at=received_at + timedelta(hours=3),
            packed_at=received_at + timedelta(days=1) if is_closed else None,
            promised_at=received_at + timedelta(days=3),
            incomplete_at=received_at + timedelta(days=1) if status == OrderStatus.INCOMPLETE else None,
            completed_at=received_at + timedelta(days=1) if status in (OrderStatus.COMPLETED, OrderStatus.DISPATCHED, OrderStatus.DELIVERED) else None,
            dispatched_at=received_at + timedelta(days=1, hours=4) if is_dispatched else None,
            delivered_at=received_at + timedelta(days=1, hours=8) if status == OrderStatus.DELIVERED else None,
            observations='Registro generado para demostración del flujo completo.',
            reference=reference,
            control_code=f'CTRL-{order_number[-4:]}',
            received_by=users['digitizer'],
            reviewed_by=users['packing'] if is_reviewed else None,
            packed_by=users['packing'] if is_closed else None,
            dispatched_by=users['packing'] if is_dispatched else None,
            delivered_by=users['supervisor'] if status == OrderStatus.DELIVERED else None,
        )
        for index, (code, quantity) in enumerate(item_specs, start=1):
            if status == OrderStatus.INCOMPLETE:
                scanned = max(0, quantity - 1) if index == len(item_specs) else quantity
            elif is_closed:
                scanned = quantity
            elif status == OrderStatus.QUALITY_CHECK:
                scanned = 1 if index == 1 else 0
            else:
                scanned = 0
            OrderItem.objects.create(
                order=order, garment_type=garments[code], quantity=quantity,
                scanned_quantity=scanned, label_code=code,
            )
        previous = OrderStatus.RECEIVED
        for next_status in (
            OrderStatus.QUALITY_CHECK,
            OrderStatus.INCOMPLETE if status == OrderStatus.INCOMPLETE else OrderStatus.COMPLETED,
            OrderStatus.DISPATCHED,
            OrderStatus.DELIVERED,
        ):
            should_create = (
                next_status == OrderStatus.QUALITY_CHECK and is_reviewed
                or next_status == OrderStatus.INCOMPLETE and status == OrderStatus.INCOMPLETE
                or next_status == OrderStatus.COMPLETED and status in (OrderStatus.COMPLETED, OrderStatus.DISPATCHED, OrderStatus.DELIVERED)
                or next_status == OrderStatus.DISPATCHED and is_dispatched
                or next_status == OrderStatus.DELIVERED and status == OrderStatus.DELIVERED
            )
            if should_create:
                OrderStatusHistory.objects.create(
                    order=order, previous_status=previous, new_status=next_status,
                    changed_by=users['supervisor'], note='Transición generada para la demostración.',
                )
                previous = next_status
        return order

    @staticmethod
    def _delivery_scan(order, user, room, delivered_at):
        SiteScan.objects.create(
            kind=SiteScan.Kind.DELIVERY, scanned_code=order.reference, order=order,
            scanned_at=delivered_at, scanned_by=user, room=room,
            client_uuid=demo_uuid(f'delivery/{order.order_number}'),
            latitude=Decimal('-24.625123'), longitude=Decimal('-70.402456'),
            accuracy_meters=7.5, note='Entrega demo georreferenciada.',
        )

    @staticmethod
    def _create_weigh_ins(now, clients, companies, orders, users):
        digitized = WeighIn.objects.create(
            reference='M1001A', client=clients['room'], company=companies['principal'],
            garment_count=4, weight_kg=Decimal('8.40'), status=WeighIn.Status.DIGITIZED,
            weighed_at=now - timedelta(hours=5), weighed_by=users['weighing'],
            order=orders['DEMO-1001'], digitized_at=now - timedelta(hours=2),
        )
        pending = WeighIn.objects.create(
            reference='M1008A', client=clients['room'], company=companies['contractor'],
            garment_count=3, weight_kg=Decimal('6.75'), status=WeighIn.Status.PENDING,
            weighed_at=now - timedelta(minutes=25), weighed_by=users['weighing'],
        )
        for weigh_in in (digitized, pending):
            WeighLabel.objects.bulk_create(
                WeighLabel(weigh_in=weigh_in, sequence=index, code=f'{weigh_in.reference}-{index:02d}')
                for index in range(1, weigh_in.garment_count + 1)
            )

    @staticmethod
    def _create_hospitality(now, company, camps, garments, users):
        specs = (
            ('H-2026-0001', BatchStatus.RECEIVED, 0, None),
            ('H-2026-0002', BatchStatus.IN_PROCESS, 2, (118, 78)),
            ('H-2026-0003', BatchStatus.DISPATCHED, 5, (116, 77)),
        )
        for number, status, days_ago, counts in specs:
            batch = LinenBatch.objects.create(
                batch_number=number, company=company, camp=camps['central'], status=status,
                received_at=now - timedelta(days=days_ago, hours=3),
                promised_at=now - timedelta(days=days_ago) + timedelta(days=4),
                dispatched_at=now - timedelta(days=1) if status == BatchStatus.DISPATCHED else None,
                weight_kg=Decimal('286.50'), observations='Lote demo de hotelería.',
                received_by=users['supervisor'],
                dispatched_by=users['packing'] if status == BatchStatus.DISPATCHED else None,
                received_by_client='Encargado de hotelería' if status == BatchStatus.DISPATCHED else '',
            )
            LinenBatchItem.objects.create(
                batch=batch, garment_type=garments['SAB'], quantity_in=120,
                quantity_out=counts[0] if counts else None, weight_kg=Decimal('210.00'),
            )
            LinenBatchItem.objects.create(
                batch=batch, garment_type=garments['TOA'], quantity_in=80,
                quantity_out=counts[1] if counts else None, weight_kg=Decimal('76.50'),
            )