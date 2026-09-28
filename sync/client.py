"""Cliente HTTP del servidor local hacia la nube (solo librería estándar)."""

import json
import urllib.error
import urllib.parse
import urllib.request

from django.conf import settings
from django.core.serializers.json import DjangoJSONEncoder


class CloudUnavailable(Exception):
    """No hay conexión con la nube (sin internet, DNS, timeout, 5xx)."""


class CloudRejected(Exception):
    """La nube respondió, pero rechazó la petición (token, versión, datos)."""

    def __init__(self, status: int, detail: str):
        super().__init__(f'{status}: {detail}')
        self.status = status
        self.detail = detail


def is_configured() -> bool:
    return bool(settings.SYNC_CLOUD_URL and settings.SYNC_NODE_TOKEN)


def request(method: str, path: str, params: dict | None = None, body: dict | None = None,
            timeout: float | None = None) -> tuple[int, bytes, dict]:
    """Petición cruda con el token del nodo. Devuelve (status, cuerpo, cabeceras)."""
    if not is_configured():
        raise CloudUnavailable('El servidor local no tiene configurada la nube (SYNC_CLOUD_URL / SYNC_NODE_TOKEN).')
    url = f'{settings.SYNC_CLOUD_URL}{path}'
    if params:
        url = f'{url}?{urllib.parse.urlencode(params)}'
    data = json.dumps(body, cls=DjangoJSONEncoder).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, method=method)
    req.add_header('Authorization', f'Bearer {settings.SYNC_NODE_TOKEN}')
    req.add_header('Accept', 'application/json')
    # La nube está detrás de Caddy con HTTPS; en la nube Django confía en esta
    # cabecera para saber que la petición llegó cifrada.
    if data is not None:
        req.add_header('Content-Type', 'application/json')
    try:
        with urllib.request.urlopen(req, timeout=timeout or settings.SYNC_HTTP_TIMEOUT_SECONDS) as response:
            return response.status, response.read(), dict(response.headers)
    except urllib.error.HTTPError as exc:
        payload = exc.read()
        if exc.code >= 500:
            raise CloudUnavailable(f'La nube respondió {exc.code}.') from exc
        return exc.code, payload, dict(exc.headers)
    except (urllib.error.URLError, TimeoutError, ConnectionError, OSError) as exc:
        raise CloudUnavailable(str(getattr(exc, 'reason', exc))) from exc


def call(method: str, path: str, params: dict | None = None, body: dict | None = None) -> dict:
    status, payload, _ = request(method, path, params=params, body=body)
    try:
        data = json.loads(payload or b'{}')
    except json.JSONDecodeError:
        data = {}
    if status >= 400:
        raise CloudRejected(status, data.get('detail', payload[:200].decode(errors='replace')))
    return data
