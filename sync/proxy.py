"""Histórico de más de 90 días: el servidor local lo consulta en la nube cuando hay internet.

El servidor local guarda solo la ventana reciente de guías (ver
`sync/window.py`). Cuando la terminal pide algo más antiguo —el listado del
histórico con un rango de fechas que se sale de la ventana, o el detalle de una
guía que ya no está— este middleware reenvía la misma petición a la nube, con
el token del nodo, y devuelve su respuesta tal cual. Los IDs son los mismos en
los dos lados, así que la terminal no nota la diferencia.

Sin internet responde lo que hay localmente, y la cabecera
`X-Servilion-Source: local` le permite a la terminal avisar que el resultado
puede estar incompleto.
"""

import logging
import re
from datetime import datetime

from django.http import HttpResponse
from django.utils.dateparse import parse_date, parse_datetime
from django.utils.timezone import is_naive, make_aware

from authentication.services import AuthError, get_user_from_token
from common.node import is_edge
from orders.models import LaundryOrder
from sync import client, state
from sync.window import cutoff

logger = logging.getLogger(__name__)

SOURCE_HEADER = 'X-Servilion-Source'
PROXY_TIMEOUT_SECONDS = 8

ORDER_LIST = re.compile(r'^/api/orders/?$')
ORDER_DETAIL = re.compile(r'^/api/orders/(?P<id>\d+)(/(history|receipt|packing|garment-labels))?/?$')


def _parse_when(value: str | None) -> datetime | None:
    if not value:
        return None
    parsed = parse_datetime(value)
    if parsed is None:
        day = parse_date(value)
        parsed = datetime(day.year, day.month, day.day) if day else None
    if parsed is not None and is_naive(parsed):
        parsed = make_aware(parsed)
    return parsed


def _authenticated(request) -> bool:
    header = request.headers.get('Authorization', '')
    if not header.startswith('Bearer '):
        return False
    try:
        get_user_from_token(header.removeprefix('Bearer ').strip(), expected_type='access')
    except AuthError:
        return False
    return True


def _needs_cloud(request) -> bool:
    path = request.path
    if ORDER_LIST.match(path):
        date_from = _parse_when(request.GET.get('date_from'))
        return date_from is None or date_from < cutoff()
    match = ORDER_DETAIL.match(path)
    if match:
        return not LaundryOrder.objects.filter(pk=int(match.group('id'))).exists()
    return False


class EdgeHistoryProxyMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        if not (is_edge() and request.method == 'GET' and client.is_configured()):
            return self.get_response(request)
        if not _needs_cloud(request) or not _authenticated(request):
            return self.get_response(request)

        # Si el proceso de sincronización ya sabe que no hay internet, no se
        # hace esperar al operador por un timeout.
        if state.get(state.ONLINE, False):
            try:
                status, body, headers = client.request(
                    'GET', request.get_full_path(), timeout=PROXY_TIMEOUT_SECONDS
                )
            except client.CloudUnavailable as exc:
                logger.info('Histórico en la nube no disponible: %s', exc)
            else:
                response = HttpResponse(body, status=status, content_type=headers.get('Content-Type', 'application/json'))
                response[SOURCE_HEADER] = 'cloud'
                return response

        response = self.get_response(request)
        response[SOURCE_HEADER] = 'local'
        return response
