from datetime import datetime

from ninja import Schema


class ChangeIn(Schema):
    table: str
    id: int
    op: str
    data: dict | None = None


class PushIn(Schema):
    changes: list[ChangeIn]
    # Cuántos cambios le quedan por enviar al servidor local después de este
    # lote: la web lo muestra para saber si la planta está al día.
    pending: int = 0


class PushOut(Schema):
    applied: int
    skipped: int
    issues: list[str]


class ChangeOut(Schema):
    table: str
    id: int
    op: str
    data: dict | None = None


class PullOut(Schema):
    changes: list[ChangeOut]
    last_id: int
    has_more: bool


class SnapshotRowOut(Schema):
    id: int
    data: dict


class SnapshotOut(Schema):
    table: str
    rows: list[SnapshotRowOut]
    last_id: int
    has_more: bool


class CursorOut(Schema):
    last_id: int


class NodeStatusOut(Schema):
    name: str
    online: bool
    last_push_at: datetime | None
    last_pull_at: datetime | None
    pending_changes: int


class SyncStatusOut(Schema):
    # 'edge' en el servidor local de planta, 'cloud' en api.servilion.cl.
    node: str
    configured: bool = False
    cloud_url: str = ''
    bootstrapped: bool = False
    online: bool = False
    pending_changes: int = 0
    last_push_at: str | None = None
    last_pull_at: str | None = None
    last_error: str | None = None
    retention_days: int = 0
    open_issues: int = 0
    # En la nube: los servidores locales registrados y cuándo se vieron.
    nodes: list[NodeStatusOut] = []


class SyncIssueOut(Schema):
    id: int
    kind: str
    table: str
    row_id: int
    detail: str
    payload: dict | None
    created_at: datetime
    resolved_at: datetime | None
