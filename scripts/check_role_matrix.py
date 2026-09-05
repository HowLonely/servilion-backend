"""Verifica la matriz de permisos por rol golpeando la API real.

Se ejecuta con:  python manage.py shell < scripts/check_role_matrix.py

Crea un usuario por rol dentro de una transacción que SIEMPRE se revierte, así
que no deja rastro en la base. Distingue 403 (el rol no puede) de cualquier otro
código (el rol sí puede; lo que falle después es de datos, no de permisos).
"""

from django.conf import settings
from django.db import transaction
from django.test import Client

from authentication.models import User
from authentication.services import issue_tokens

# `Client` envía Host: testserver, que ALLOWED_HOSTS rechaza con 400 y taparía
# los 403 que queremos medir. Solo afecta a este proceso de shell.
if 'testserver' not in settings.ALLOWED_HOSTS:
    settings.ALLOWED_HOSTS.append('testserver')

ROLES = ['ADMIN', 'SUPERVISOR', 'PESAJE', 'DIGITADOR_OT', 'DIGITADOR_EMPAQUE']

# (etiqueta, método, ruta, cuerpo, roles que DEBEN poder)
CASES = [
    # El cuerpo tiene que pasar el esquema: Ninja valida ANTES de entrar a la
    # vista, y un 422 taparía el 403 que queremos medir (ver `can` abajo).
    ('Registrar pesaje', 'post', '/api/weighing/',
     {'client_id': 999999, 'company_id': 999999, 'garment_count': 1, 'weight_kg': 1.0},
     {'ADMIN', 'SUPERVISOR', 'PESAJE'}),

    ('Anular pesaje', 'post', '/api/weighing/999999/void',
     {'note': 'X'},
     {'ADMIN', 'SUPERVISOR', 'PESAJE'}),

    ('Digitalizar OT', 'post', '/api/orders/',
     {'order_number': 'TEST-PERM', 'worker_id': 999999, 'received_at': '2026-01-01T00:00:00Z', 'items': []},
     {'ADMIN', 'SUPERVISOR', 'DIGITADOR_OT'}),

    ('Pistoleo unico de empaque', 'post', '/api/orders/scan/packing',
     {'code': 'X', 'quantity': 1},
     {'ADMIN', 'SUPERVISOR', 'DIGITADOR_EMPAQUE'}),

    ('Pistolear empaque', 'post', '/api/orders/999999/packing/scan',
     {'code': 'X', 'quantity': 1},
     {'ADMIN', 'SUPERVISOR', 'DIGITADOR_EMPAQUE'}),

    ('Cerrar empaque', 'post', '/api/orders/999999/packing/finish',
     {'note': ''},
     {'ADMIN', 'SUPERVISOR', 'DIGITADOR_EMPAQUE'}),

    ('Despachar morral', 'post', '/api/orders/999999/dispatch',
     {'note': ''},
     {'ADMIN', 'SUPERVISOR', 'DIGITADOR_EMPAQUE'}),

    ('Recepción morral limpio', 'post', '/api/orders/999999/clean-reception',
     {'note': ''},
     {'ADMIN', 'SUPERVISOR'}),

    ('Entrega en habitación', 'post', '/api/orders/999999/deliver',
     {'note': ''},
     {'ADMIN', 'SUPERVISOR'}),

    ('Torre de control', 'get', '/api/reports/operations/summary', None,
     {'ADMIN', 'SUPERVISOR'}),

    # Hoteleria reparte sus tres momentos entre los mismos puestos que el morral,
    # salvo el despacho: ahi se fija la merma definitiva del lote y queda en el
    # supervisor.
    ('Recibir carga de lenceria', 'post', '/api/hospitality/',
     {'company_id': 999999, 'items': [{'quantity_in': 1, 'custom_name': 'X'}]},
     {'ADMIN', 'SUPERVISOR', 'DIGITADOR_OT'}),

    ('Contar salida de lenceria', 'post', '/api/hospitality/999999/return-count',
     {'counts': []},
     {'ADMIN', 'SUPERVISOR', 'DIGITADOR_EMPAQUE'}),

    ('Despachar lote de lenceria', 'post', '/api/hospitality/999999/dispatch',
     {'received_by_client': '', 'note': ''},
     {'ADMIN', 'SUPERVISOR'}),

    ('Conflictos de sincronización', 'get', '/api/orders/sync-conflicts', None,
     {'ADMIN'}),

    ('Crear trabajador', 'post', '/api/workers/',
     {'company_id': 999999, 'badge_code': 'X', 'full_name': 'X'},
     {'ADMIN'}),

    ('Crear prenda', 'post', '/api/garments/',
     {'code': 'ZZZ', 'name': 'X'},
     {'ADMIN'}),

    ('Crear empresa', 'post', '/api/companies/',
     {'name': 'X'},
     {'ADMIN'}),
]


def run():
    client = Client()
    tokens = {}
    for role in ROLES:
        user = User.objects.create_user(
            username=f'__perm_check_{role}', password='x', role=role
        )
        tokens[role] = issue_tokens(user)['access']

    ok = True
    print(f"\n{'Acción':<32}" + ''.join(f'{r:<20}' for r in ROLES))
    print('-' * (32 + 20 * len(ROLES)))

    for label, method, path, body, allowed in CASES:
        cells = []
        for role in ROLES:
            kwargs = {'HTTP_AUTHORIZATION': f'Bearer {tokens[role]}'}
            if body is not None:
                kwargs['data'] = body
                kwargs['content_type'] = 'application/json'
            status = getattr(client, method)(path, **kwargs).status_code

            can = status != 403
            expected = role in allowed
            mark = 'SI ' if can else 'NO '
            if can != expected:
                ok = False
                mark = f'ERROR({status})'
            cells.append(f'{mark} [{status}]')
        print(f'{label:<32}' + ''.join(f'{c:<20}' for c in cells))

    print()
    print('RESULTADO:', 'matriz correcta' if ok else 'HAY DIFERENCIAS')
    return ok


try:
    with transaction.atomic():
        result = run()
        raise RuntimeError('rollback intencional')
except RuntimeError as exc:
    if str(exc) != 'rollback intencional':
        raise
    print('(usuarios de prueba revertidos)')
