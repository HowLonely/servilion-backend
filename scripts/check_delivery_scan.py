"""Verifica el doble escaneo de entrega (QR de la OT + QR de la puerta).

Se ejecuta con:  python manage.py shell < scripts/check_delivery_scan.py

Monta un escenario completo dentro de una transacción que SIEMPRE se revierte.
"""

from django.conf import settings
from django.db import transaction
from django.test import Client as HttpClient
from django.utils import timezone

from authentication.models import User
from authentication.services import issue_tokens
from companies.models import Client, Company
from camps.models import Camp, Room
from garments.models import GarmentType
from orders.models import LaundryOrder, OrderStatus, SiteScan
from workers.models import Worker

if 'testserver' not in settings.ALLOWED_HOSTS:
    settings.ALLOWED_HOSTS.append('testserver')

PASS, FAIL = 'OK  ', 'FALLA'


def check(label: str, condition: bool, extra: str = '') -> bool:
    print(f'{PASS if condition else FAIL}  {label}{f" -> {extra}" if extra else ""}')
    return condition


def run() -> bool:
    http = HttpClient()
    user = User.objects.create_user(username='__deliv_sup', password='x', role=User.Role.SUPERVISOR)
    token = issue_tokens(user)['access']
    auth = {'HTTP_AUTHORIZATION': f'Bearer {token}'}

    cliente = Client.objects.create(name='__CLI_TEST')
    empresa = Company.objects.create(
        client=cliente, name='__EMP_TEST', delivery_flow=Company.DeliveryFlow.WITH_ROOM_DELIVERY
    )
    campamento = Camp.objects.create(client=cliente, name='ALFA')
    pieza_ok = Room.objects.create(camp=campamento, number='101')
    pieza_otra = Room.objects.create(camp=campamento, number='202')
    GarmentType.objects.get_or_create(code='__T1', defaults={'name': 'Test'})

    trabajador = Worker.objects.create(
        company=empresa, badge_code='__W1', full_name='Test Worker', current_room=pieza_ok
    )

    def nueva_guia(ref: str) -> LaundryOrder:
        order = LaundryOrder.objects.create(
            order_number=ref, worker=trabajador, company=empresa,
            status=OrderStatus.COMPLETED, received_at=timezone.now(),
            completed_at=timezone.now(), reference=ref, delivery_room=pieza_ok,
        )
        # `register_delivery` exige que el morral ya haya llegado a faena.
        SiteScan.objects.create(
            kind=SiteScan.Kind.CLEAN_IN, scanned_code=ref, order=order, scanned_at=timezone.now()
        )
        return order

    def post(body: dict):
        return http.post('/api/delivery/confirm', data=body, content_type='application/json', **auth)

    ok = True

    # 1. Pieza correcta -> entrega registrada
    g1 = nueva_guia('__OT1')
    r = post({'order_code': '__OT1', 'room_qr': str(pieza_ok.qr_code)})
    body = r.json()
    g1.refresh_from_db()
    ok &= check('Pieza correcta responde 200', r.status_code == 200, str(r.status_code))
    ok &= check('Guía queda ENTREGADA', g1.status == OrderStatus.DELIVERED, g1.status)
    ok &= check('room_matched = True', body.get('room_matched') is True)
    scan = SiteScan.objects.filter(order=g1, kind=SiteScan.Kind.DELIVERY).first()
    ok &= check('Pistoleo guarda la habitación', scan is not None and scan.room_id == pieza_ok.id)

    # 2. Pieza equivocada -> 409 sin entregar
    g2 = nueva_guia('__OT2')
    r = post({'order_code': '__OT2', 'room_qr': str(pieza_otra.qr_code)})
    g2.refresh_from_db()
    ok &= check('Pieza equivocada responde 409', r.status_code == 409, str(r.status_code))
    ok &= check('Guía NO cambió de estado', g2.status == OrderStatus.COMPLETED, g2.status)
    ok &= check('409 informa ambas piezas',
                r.json().get('expected_room', {}).get('number') == '101'
                and r.json().get('scanned_room', {}).get('number') == '202')

    # 3. Pieza equivocada + confirmación explícita -> entrega con discrepancia anotada
    r = post({'order_code': '__OT2', 'room_qr': str(pieza_otra.qr_code), 'confirm_different_room': True})
    g2.refresh_from_db()
    ok &= check('Con confirmación responde 200', r.status_code == 200, str(r.status_code))
    ok &= check('Guía queda ENTREGADA', g2.status == OrderStatus.DELIVERED, g2.status)
    ok &= check('room_matched = False', r.json().get('room_matched') is False)
    scan2 = SiteScan.objects.filter(order=g2, kind=SiteScan.Kind.DELIVERY).first()
    ok &= check('Discrepancia anotada en el pistoleo', scan2 is not None and '202' in scan2.note, scan2.note if scan2 else '')

    # 4. QR inexistente -> 404
    r = post({'order_code': '__OT1', 'room_qr': '00000000-0000-0000-0000-000000000000'})
    ok &= check('QR inexistente responde 404', r.status_code == 404, str(r.status_code))

    # 5. OT inexistente -> 404
    r = post({'order_code': 'NO_EXISTE', 'room_qr': str(pieza_ok.qr_code)})
    ok &= check('OT inexistente responde 404', r.status_code == 404, str(r.status_code))

    # 6. Guía en estado que no permite entrega -> 400
    g3 = LaundryOrder.objects.create(
        order_number='__OT3', worker=trabajador, company=empresa, status=OrderStatus.RECEIVED,
        received_at=timezone.now(), reference='__OT3', delivery_room=pieza_ok,
    )
    r = post({'order_code': '__OT3', 'room_qr': str(pieza_ok.qr_code)})
    g3.refresh_from_db()
    ok &= check('Guía sin recepción en faena responde 400', r.status_code == 400, str(r.status_code))
    ok &= check('Guía NO cambió de estado', g3.status == OrderStatus.RECEIVED, g3.status)

    # 7. Flujo 2 (entrega solo al cliente) -> 400
    empresa2 = Company.objects.create(
        client=cliente, name='__EMP_F2', delivery_flow=Company.DeliveryFlow.CLIENT_ONLY
    )
    w2 = Worker.objects.create(company=empresa2, badge_code='__W2', full_name='F2', current_room=pieza_ok)
    g4 = LaundryOrder.objects.create(
        order_number='__OT4', worker=w2, company=empresa2, status=OrderStatus.COMPLETED,
        received_at=timezone.now(), reference='__OT4', delivery_room=pieza_ok,
    )
    SiteScan.objects.create(
        kind=SiteScan.Kind.CLEAN_IN, scanned_code='__OT4', order=g4, scanned_at=timezone.now()
    )
    r = post({'order_code': '__OT4', 'room_qr': str(pieza_ok.qr_code)})
    ok &= check('Flujo 2 rechaza la entrega', r.status_code == 400, str(r.status_code))

    print()
    print('RESULTADO:', 'todo correcto' if ok else 'HAY FALLAS')
    return ok


try:
    with transaction.atomic():
        run()
        raise RuntimeError('rollback intencional')
except RuntimeError as exc:
    if str(exc) != 'rollback intencional':
        raise
    print('(datos de prueba revertidos)')
