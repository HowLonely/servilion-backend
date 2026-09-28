from datetime import timedelta
from typing import List

from django.utils import timezone
from ninja import Router
from ninja.pagination import paginate
from ninja.security import HttpBearer

from authentication.auth import JWTAuth
from authentication.permissions import Perm, require_permission
from common.node import is_edge
from common.schemas import MessageOut
from sync import cloud, edge
from sync.models import Node, SyncIssue
from sync.schemas import (
    CursorOut,
    PullOut,
    PushIn,
    PushOut,
    SnapshotOut,
    SyncIssueOut,
    SyncStatusOut,
)

router = Router()

# Un servidor local sondea cada pocos segundos: si no se lo ve en un minuto,
# está sin internet (o apagado).
NODE_ONLINE_WINDOW = timedelta(minutes=1)


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


@router.get('/status', response=SyncStatusOut, auth=JWTAuth())
def status(request):
    """Estado de la sincronización, para la cabecera de la terminal y el panel web."""
    if is_edge():
        return edge.status()
    now = timezone.now()
    nodes = [
        {
            'name': node.name,
            'online': bool(node.last_pull_at and now - node.last_pull_at < NODE_ONLINE_WINDOW),
            'last_push_at': node.last_push_at,
            'last_pull_at': node.last_pull_at,
            'pending_changes': node.reported_pending,
        }
        for node in Node.objects.filter(is_active=True)
    ]
    return {
        'node': 'cloud',
        'configured': True,
        'online': True,
        'open_issues': SyncIssue.objects.filter(resolved_at__isnull=True).count(),
        'nodes': nodes,
    }


@router.get('/issues', response=List[SyncIssueOut], auth=JWTAuth())
@require_permission(Perm.SYNC)
@paginate
def list_issues(request, resolved: bool | None = None):
    queryset = SyncIssue.objects.all()
    if resolved is not None:
        queryset = queryset.filter(resolved_at__isnull=not resolved)
    return queryset.order_by('-created_at')


@router.post('/issues/{issue_id}/resolve', response=SyncIssueOut, auth=JWTAuth())
@require_permission(Perm.SYNC)
def resolve_issue(request, issue_id: int):
    issue = SyncIssue.objects.get(pk=issue_id)
    issue.resolved_at = timezone.now()
    issue.resolved_by = request.auth
    issue.save(update_fields=['resolved_at', 'resolved_by'])
    return issue
