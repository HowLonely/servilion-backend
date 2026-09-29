"""Endpoints a los que se conectan los servidores locales: solo existen en la nube.

Se montan en `core/api.py` únicamente cuando el nodo es la nube, y la imagen
del servidor local (`Dockerfile.edge`) no trae este módulo ni `sync/cloud.py`.
"""

from ninja import Router
from ninja.security import HttpBearer

from common.node import is_edge
from common.schemas import MessageOut
from sync import cloud
from sync.models import Node
from sync.schemas import CursorOut, PullOut, PushIn, PushOut, SnapshotOut

router = Router()


class NodeAuth(HttpBearer):
    """Solo servidores locales registrados (`srvnode_...`), no personas."""

    def authenticate(self, request, token: str) -> Node | None:
        node = cloud.node_from_token(token)
        if node is not None:
            request.user = node.service_user
        return node


def _cloud_only():
    if is_edge():
        return 400, {'detail': 'Este endpoint es de la nube; el servidor local no expone su feed.'}
    return None


@router.post('/push', response={200: PushOut, 400: MessageOut}, auth=NodeAuth())
def push(request, payload: PushIn):
    """El servidor local entrega su outbox: la operación de planta."""
    if (refused := _cloud_only()) is not None:
        return refused
    result = cloud.receive_push(request.auth, [change.dict() for change in payload.changes], payload.pending)
    return 200, {'applied': result.applied, 'skipped': result.skipped, 'issues': result.issues}


@router.get('/pull', response={200: PullOut, 400: MessageOut, 410: MessageOut}, auth=NodeAuth())
def pull(request, after: int = 0, limit: int = 500):
    """Cambios hechos en la nube (web, app móvil) posteriores al cursor del servidor local."""
    if (refused := _cloud_only()) is not None:
        return refused
    try:
        return 200, cloud.read_feed(request.auth, after, min(limit, 2000))
    except cloud.ResetRequired as exc:
        return 410, {'detail': str(exc)}


@router.get('/cursor', response={200: CursorOut, 400: MessageOut}, auth=NodeAuth())
def cursor(request):
    """Punto del feed desde el que leer después de bajar la foto completa."""
    if (refused := _cloud_only()) is not None:
        return refused
    return 200, {'last_id': cloud.current_head()}


@router.get('/snapshot', response={200: SnapshotOut, 400: MessageOut}, auth=NodeAuth())
def snapshot(request, table: str, after_id: int = 0, limit: int = 1000, retention_days: int = 90):
    """Foto de una tabla para instalar un servidor local."""
    if (refused := _cloud_only()) is not None:
        return refused
    try:
        return 200, cloud.read_snapshot(table, after_id, min(limit, 5000), retention_days)
    except ValueError as exc:
        return 400, {'detail': str(exc)}
