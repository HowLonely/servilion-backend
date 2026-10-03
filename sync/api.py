from datetime import timedelta
from typing import List

from django.utils import timezone
from ninja import Router
from ninja.pagination import paginate

from authentication.auth import JWTAuth
from authentication.permissions import Perm, require_permission
from common.node import is_edge
from sync import edge
from sync.models import Node, SyncIssue
from sync.schemas import SyncIssueOut, SyncStatusOut

router = Router()

# Un servidor local sondea cada pocos segundos: si no se lo ve en un minuto,
# está sin internet (o apagado).
NODE_ONLINE_WINDOW = timedelta(minutes=1)


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
