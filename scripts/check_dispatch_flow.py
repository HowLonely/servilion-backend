"""Verifica el empaque (abrir/cerrar) y el despacho como módulo aparte.

Se ejecuta con:  python manage.py shell < scripts/check_dispatch_flow.py

Monta un escenario completo dentro de una transacción que SIEMPRE se revierte.
Cubre la separación del despacho (paso 7) de la mesa de empaque: pistolear la
boleta ahí abre y cierra el morral nada más, un tercer disparo ya no despacha
y solo avisa que hay que ir al módulo Despacho, que despacha por
`POST /{id}/dispatch`. Ver `scan_packing_code` y `dispatch_order` en
orders/services.py.
"""

from django.conf import settings
from django.db import transaction
from django.test import Client as HttpClient
from django.utils import timezone

from authentication.models import User
from authentication.services import issue_tokens
from companies.models import Client, Company
from garments.models import GarmentType
from orders.models import LaundryOrder, OrderItem, OrderStatus
from workers.models import Worker

if 'testserver' not in settings.ALLOWED_HOSTS:
    settings.ALLOWED_HOSTS.append('testserver')

PASS, FAIL = 'OK  ', 'FALLA'


def check(label: str, condition: bool, extra: str = '') -> bool:
    print(f'{PASS if condition else FAIL}  {label}{f" -> {extra}" if extra else ""}')
    return condition


def run() -> bool:
    http = HttpClient()
    user = User.objects.create_user(username='__desp_sup', password='x', role=User.Role.SUPERVISOR)
    token = issue_tokens(user)['access']
    auth = {'HTTP_AUTHORIZATION': f'Bearer {token}'}

    cliente = Client.objects.create(name='__CLI_DESP')
    empresa = Company.objects.create(
        client=cliente, name='__EMP_DESP', delivery_flow=Company.DeliveryFlow.WITH_ROOM_DELIVERY
    )
    tipo, _ = GarmentType.objects.get_or_create(code='__TOA', defaults={'name': 'Toalla'})
    trabajador = Worker.objects.create(
        company=empresa, badge_code='__WD1', full_name='Despacho Test'
    )

    def nueva_guia(ref: str, cantidad: int = 1) -> LaundryOrder:
        order = LaundryOrder.objects.create(
            order_number=ref, worker=trabajador, company=empresa,
            status=OrderStatus.RECEIVED, received_at=timezone.now(),
            reference=ref, garment_count=cantidad,
        )
        OrderItem.objects.create(order=order, garment_type=tipo, quantity=cantidad, label_code='__TOA')
        return order

    def pistolear(code: str):
        return http.post(
            '/api/orders/scan/packing', data={'code': code},
            content_type='application/json', **auth,
        )

    def despachar(order_id: int):
        return http.post(
            f'/api/orders/{order_id}/dispatch', data={'note': ''},
            content_type='application/json', **auth,
        )

    ok = True

    # --- 1. Morral completo: abrir -> prenda -> cerrar -> Despacho ----------
    g1 = nueva_guia('__D1')
    r = pistolear('__D1')
    g1.refresh_from_db()
    ok &= check('1er disparo abre el morral', r.status_code == 200 and r.json()['action'] == 'ABIERTO',
                f'{r.status_code} {r.json().get("action") or r.json()}')
    ok &= check('   queda EN_REVISION', g1.status == OrderStatus.QUALITY_CHECK, g1.status)

    r = pistolear('__D1-__TOA')
    ok &= check('Etiqueta de prenda se pistolea', r.status_code == 200 and r.json()['action'] == 'PISTOLEADA',
                f'{r.status_code} {r.json().get("action") or r.json()}')

    r = pistolear('__D1')
    g1.refresh_from_db()
    ok &= check('2do disparo cierra el morral', r.status_code == 200 and r.json()['action'] == 'CERRADO',
                f'{r.status_code} {r.json().get("action") or r.json()}')
    ok &= check('   queda COMPLETADA (cerrada, aún en planta)', g1.status == OrderStatus.COMPLETED, g1.status)
    ok &= check('   sin dispatched_at todavía', g1.dispatched_at is None, str(g1.dispatched_at))

    r = pistolear('__D1')
    g1.refresh_from_db()
    ok &= check('3er disparo en empaque YA NO despacha', r.status_code == 400, str(r.status_code))
    ok &= check('   el error nombra el estado y manda a Despacho',
                'Completa' in r.json().get('detail', '') and 'Despacho' in r.json().get('detail', ''),
                r.json().get('detail', ''))
    ok &= check('   sigue COMPLETADA, nadie la despachó sola', g1.status == OrderStatus.COMPLETED, g1.status)

    r = despachar(g1.id)
    g1.refresh_from_db()
    ok &= check('El módulo Despacho despacha por id', r.status_code == 200, str(r.status_code))
    ok &= check('   queda DESPACHADA', g1.status == OrderStatus.DISPATCHED, g1.status)
    ok &= check('   con dispatched_at y dispatched_by', g1.dispatched_at is not None and g1.dispatched_by_id == user.id)

    r = pistolear('__D1')
    ok &= check('Pistolear en empaque una guía ya despachada tampoco hace nada',
                r.status_code == 400, str(r.status_code))
    ok &= check('   el error nombra el estado', 'Despachada' in r.json().get('detail', ''), r.json().get('detail', ''))

    # --- 2. Morral incompleto: se cierra incompleto y Despacho lo saca igual
    g2 = nueva_guia('__D2', cantidad=2)
    pistolear('__D2')                 # abre
    pistolear('__D2-__TOA')           # solo 1 de 2 prendas
    r = pistolear('__D2')             # cierra
    g2.refresh_from_db()
    ok &= check('Morral con faltante cierra como INCOMPLETA', g2.status == OrderStatus.INCOMPLETE, g2.status)
    ok &= check('   con incomplete_at', g2.incomplete_at is not None)

    r = pistolear('__D2')
    ok &= check('3er disparo tampoco despacha el incompleto', r.status_code == 400, str(r.status_code))

    r = despachar(g2.id)
    g2.refresh_from_db()
    ok &= check('El módulo Despacho despacha el morral incompleto', r.status_code == 200, str(r.status_code))
    ok &= check('   queda DESPACHADA conservando incomplete_at',
                g2.status == OrderStatus.DISPATCHED and g2.incomplete_at is not None, g2.status)

    # --- 3. La prenda que faltaba aparece DESPUÉS del despacho -------------
    r = pistolear('__D2-__TOA')
    g2.refresh_from_db()
    ok &= check('La prenda reaparecida se resuelve post-despacho',
                r.status_code == 200 and r.json()['action'] == 'ENCONTRADA',
                f'{r.status_code} {r.json().get("action") or r.json()}')
    ok &= check('   la guía NO vuelve a COMPLETADA', g2.status == OrderStatus.DISPATCHED, g2.status)
    ok &= check('   queda packed_at (faltante saldado)', g2.packed_at is not None)
    ok &= check('   con la resolución registrada',
                g2.missing_item_resolutions.filter(resolution_type='ENCONTRADA').exists())

    # --- 3b. La prenda resuelta viaja en su propio envío, también por Despacho
    pendiente = g2.missing_item_resolutions.filter(shipped_at__isnull=True)
    ok &= check('La prenda resuelta queda pendiente de envío', pendiente.count() == 1,
                str(pendiente.count()))

    r = pistolear('__D2')
    ok &= check('Pistolear en empaque no envía la prenda pendiente (eso es de Despacho)',
                r.status_code == 400, str(r.status_code))

    r = despachar(g2.id)
    g2.refresh_from_db()
    ok &= check('El módulo Despacho envía la prenda en su envío aparte', r.status_code == 200, str(r.status_code))
    ok &= check('   la guía sigue DESPACHADA (no cambia de estado)',
                g2.status == OrderStatus.DISPATCHED, g2.status)
    ok &= check('   la resolución queda sellada con shipped_at',
                not g2.missing_item_resolutions.filter(shipped_at__isnull=True).exists())
    ok &= check('   el envío queda en el historial',
                g2.status_history.filter(note__icontains='envío aparte').exists())

    r = despachar(g2.id)
    ok &= check('Sin prendas pendientes, Despacho ya no tiene nada que hacer', r.status_code == 400,
                str(r.status_code))

    # --- 4. Recepción en faena exige despacho ------------------------------
    g3 = nueva_guia('__D3')
    pistolear('__D3')
    pistolear('__D3-__TOA')
    pistolear('__D3')                 # cierra: COMPLETADA
    r = http.post(f'/api/orders/{g3.id}/clean-reception', data={'note': ''},
                  content_type='application/json', **auth)
    ok &= check('Faena rechaza un morral sin despachar', r.status_code == 400, str(r.status_code))

    despachar(g3.id)                  # despacha, ahora desde Despacho
    r = http.post(f'/api/orders/{g3.id}/clean-reception', data={'note': ''},
                  content_type='application/json', **auth)
    ok &= check('Faena acepta el morral despachado', r.status_code == 200, str(r.status_code))

    # --- 5. Repetir el despacho sobre una guía recién despachada -----------
    g4 = nueva_guia('__D4')
    pistolear('__D4')
    pistolear('__D4-__TOA')
    pistolear('__D4')                 # cierra
    r = despachar(g4.id)
    g4.refresh_from_db()
    ok &= check('POST /dispatch despacha por id', r.status_code == 200, str(r.status_code))
    ok &= check('   queda DESPACHADA', g4.status == OrderStatus.DISPATCHED, g4.status)
    r = despachar(g4.id)
    ok &= check('POST /dispatch repetido responde 400', r.status_code == 400, str(r.status_code))

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
