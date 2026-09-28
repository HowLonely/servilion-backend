"""Lado nube: registro de servidores locales, recepción de su outbox y feed de cambios."""

import hashlib
import secrets
from datetime import timedelta

from django.db import transaction
from django.db.models import Min
from django.utils import timezone

from authentication.models import User
from sync import feed, state
from sync.apply import SOURCE_EDGE, ApplyResult, apply_changes
from sync.models import ChangeLog, Node
from sync.registry import get_spec
from sync.serialization import serialize
from sync.window import cloud_snapshot_queryset

TOKEN_PREFIX = 'srvnode_'
# Si ningún servidor local lee el feed (o uno se da de baja sin avisar), el
# changelog no puede crecer para siempre. Pasado este plazo se borra igual, y
# el servidor que vuelva después tendrá que bajar la foto completa de nuevo.
CHANGELOG_MAX_AGE = timedelta(days=60)


class ResetRequired(Exception):
    """El cursor del servidor local apunta a cambios que la nube ya borró."""


def hash_token(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def is_node_token(token: str) -> bool:
    return token.startswith(TOKEN_PREFIX)


def node_from_token(token: str) -> Node | None:
    if not is_node_token(token):
        return None
    return (
        Node.objects.select_related('service_user')
        .filter(token_hash=hash_token(token), is_active=True)
        .first()
    )


@transaction.atomic
def register_node(name: str) -> tuple[Node, str]:
    """Da de alta un servidor local y devuelve su token (solo se muestra esta vez)."""
    token = f'{TOKEN_PREFIX}{secrets.token_urlsafe(32)}'
    username = f'nodo-{name}'.lower()[:150]
    service_user, _ = User.objects.get_or_create(
        username=username,
        defaults={'role': User.Role.ADMIN, 'is_service_account': True, 'first_name': f'Servidor {name}'},
    )
    service_user.is_service_account = True
    service_user.role = User.Role.ADMIN
    service_user.is_active = True
    service_user.set_unusable_password()
    service_user.save()
    node, _ = Node.objects.update_or_create(
        name=name,
        defaults={'token_hash': hash_token(token), 'service_user': service_user, 'is_active': True},
    )
    return node, token


def receive_push(node: Node, changes: list[dict], pending: int) -> ApplyResult:
    result = apply_changes(changes, source=SOURCE_EDGE)
    Node.objects.filter(pk=node.pk).update(last_push_at=timezone.now(), reported_pending=pending)
    return result


def current_head() -> int:
    return max(feed.head(), state.get(state.CHANGELOG_FLOOR, 0))


def read_feed(node: Node, after: int, limit: int) -> dict:
    """Cambios de la nube posteriores a `after` para el servidor local.

    `after` también es el acuse: todo lo que el servidor local ya leyó no se le
    vuelve a mandar y, cuando todos los servidores lo leyeron, se borra.
    """
    floor = state.get(state.CHANGELOG_FLOOR, 0)
    if after < floor:
        raise ResetRequired(
            f'El servidor local quedó en el cambio {after} y la nube ya borró hasta el {floor}.'
        )

    Node.objects.filter(pk=node.pk).update(pull_cursor=after, last_pull_at=timezone.now())
    trim_changelog()

    changes, last_id = feed.collect(after, limit)
    return {
        'changes': changes,
        'last_id': last_id,
        'has_more': ChangeLog.objects.filter(id__gt=last_id).exists(),
    }


def trim_changelog() -> None:
    """Borra lo que ya leyeron todos los servidores locales, y lo muy viejo."""
    acked = Node.objects.filter(is_active=True).aggregate(cursor=Min('pull_cursor'))['cursor']
    deleted_up_to = 0
    if acked:
        if ChangeLog.objects.filter(id__lte=acked).delete()[0]:
            deleted_up_to = acked
    stale = ChangeLog.objects.filter(created_at__lt=timezone.now() - CHANGELOG_MAX_AGE)
    stale_max = stale.order_by('-id').values_list('id', flat=True).first()
    if stale_max:
        stale.delete()
        deleted_up_to = max(deleted_up_to, stale_max)
    # Solo los borrados por antigüedad fuerzan una resincronización: lo acusado
    # ya estaba en el servidor. El piso se guarda igual para no retroceder.
    if stale_max and stale_max > state.get(state.CHANGELOG_FLOOR, 0):
        state.put(state.CHANGELOG_FLOOR, stale_max)


def read_snapshot(table: str, after_id: int, limit: int, retention_days: int) -> dict:
    """Foto de una tabla, paginada por id, para instalar un servidor local."""
    spec = get_spec(table)
    rows = list(cloud_snapshot_queryset(spec, retention_days).filter(id__gt=after_id).order_by('id')[:limit])
    return {
        'table': table,
        'rows': [{'id': row.pk, 'data': serialize(spec, row)} for row in rows],
        'last_id': rows[-1].pk if rows else after_id,
        'has_more': len(rows) == limit,
    }
