from typing import Any

from sync.models import SyncState

# Servidor local
PULL_CURSOR = 'pull_cursor'
BOOTSTRAPPED_AT = 'bootstrapped_at'
LAST_PUSH_AT = 'last_push_at'
LAST_PULL_AT = 'last_pull_at'
LAST_PRUNE_AT = 'last_prune_at'
LAST_ERROR = 'last_error'
ONLINE = 'online'
# Nube: hasta qué id se borró el changelog. Un servidor local con cursor por
# debajo de esto perdió cambios y tiene que volver a bajar la foto completa.
CHANGELOG_FLOOR = 'changelog_floor'


def get(key: str, default: Any = None) -> Any:
    row = SyncState.objects.filter(key=key).first()
    return row.value if row is not None and row.value is not None else default


def put(key: str, value: Any) -> None:
    SyncState.objects.update_or_create(key=key, defaults={'value': value})


def get_all() -> dict[str, Any]:
    return dict(SyncState.objects.values_list('key', 'value'))
