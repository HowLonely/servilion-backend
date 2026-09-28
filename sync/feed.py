"""Lectura del changelog: arma los lotes de cambios que viajan al otro nodo.

Lo usan los dos lados: el servidor local para su outbox (lo que envía a la
nube) y la nube para su feed (lo que el servidor local le pide).
"""

from django.db.models import Max

from sync.models import ChangeLog
from sync.registry import by_table
from sync.serialization import is_excluded, serialize


def collect(after_id: int, limit: int) -> tuple[list[dict], int]:
    """Cambios con id > `after_id`, a lo sumo `limit` entradas del changelog.

    Varias entradas de la misma fila se funden en una, con su estado actual. Si
    la fila ya no existe se informa como borrado. Devuelve los cambios y el
    último id de changelog cubierto.
    """
    entries = list(
        ChangeLog.objects.filter(id__gt=after_id).order_by('id').values('id', 'table', 'row_id', 'op')[:limit]
    )
    if not entries:
        return [], after_id

    last_id = entries[-1]['id']
    latest: dict[tuple[str, int], str] = {}
    for entry in entries:
        latest[(entry['table'], entry['row_id'])] = entry['op']

    specs = by_table()
    ids_by_table: dict[str, list[int]] = {}
    for (table, row_id), op in latest.items():
        if table in specs and op == 'U':
            ids_by_table.setdefault(table, []).append(row_id)

    rows: dict[tuple[str, int], object] = {}
    for table, ids in ids_by_table.items():
        for instance in specs[table].model._base_manager.filter(pk__in=ids):
            rows[(table, instance.pk)] = instance

    changes = []
    for (table, row_id), op in latest.items():
        spec = specs.get(table)
        if spec is None:
            continue
        instance = rows.get((table, row_id))
        if op == 'D' or instance is None:
            changes.append({'table': table, 'id': row_id, 'op': 'D', 'data': None})
        elif not is_excluded(spec, instance):
            changes.append({'table': table, 'id': row_id, 'op': 'U', 'data': serialize(spec, instance)})
    return changes, last_id


def head() -> int:
    """Último id del changelog (0 si está vacío)."""
    return ChangeLog.objects.aggregate(last=Max('id'))['last'] or 0
