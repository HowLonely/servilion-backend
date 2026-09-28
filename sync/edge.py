"""Lado servidor local: envía su outbox a la nube, baja el feed y se instala la primera vez."""

import logging

from django.conf import settings
from django.db import connection, transaction
from django.db.models import Q
from django.utils import timezone

from orders.models import LaundryOrder, SiteScan
from orders.services import attach_pending_site_scans
from sync import client, feed, state, triggers
from sync.apply import SOURCE_CLOUD, apply_changes, apply_row
from sync.models import ChangeLog, SyncIssue
from sync.registry import SYNCED_MODELS

logger = logging.getLogger(__name__)

PUSH_BATCH = 300
PULL_BATCH = 500
SNAPSHOT_BATCH = 1000


class BootstrapRefused(Exception):
    """El servidor local ya tiene datos propios y no se puede reemplazar sin --force."""


# --- IDs ---------------------------------------------------------------------


def ensure_sequences() -> None:
    """Lleva los contadores de ID de las tablas sincronizadas al rango del servidor local.

    La nube numera desde 1 y el servidor local desde `SYNC_EDGE_ID_START`. Así
    una fila creada en cualquier lado conserva su ID en el otro, y la terminal,
    la web y la app móvil se refieren a la misma guía con el mismo número.
    Es idempotente: solo sube los contadores, nunca los baja.
    """
    start = settings.SYNC_EDGE_ID_START
    with connection.cursor() as cursor:
        for spec in SYNCED_MODELS:
            table = spec.table
            cursor.execute("SELECT pg_get_serial_sequence(%s, 'id')", [table])
            sequence = cursor.fetchone()[0]
            if not sequence:
                continue
            cursor.execute(
                f'SELECT setval(%s, GREATEST(%s, (SELECT COALESCE(MAX(id), 0) + 1 FROM "{table}"),'
                f' (SELECT CASE WHEN is_called THEN last_value + 1 ELSE last_value END FROM {sequence})), false)',
                [sequence, start],
            )


# --- Envío (servidor local -> nube) -----------------------------------------


def pending_count() -> int:
    return ChangeLog.objects.count()


def push_once() -> int:
    """Envía un lote del outbox. Devuelve cuántas filas viajaron (0 = al día)."""
    changes, last_id = feed.collect(0, PUSH_BATCH)
    if last_id == 0:
        return 0
    if changes:
        response = client.call('POST', '/api/sync/push', body={
            'changes': changes,
            'pending': max(pending_count() - len(changes), 0),
        })
        for issue in response.get('issues', []):
            logger.warning('La nube no pudo aplicar %s', issue)
    # Solo se borra lo que cubrió este lote: lo anotado mientras tanto queda
    # con id mayor y sale en el próximo.
    ChangeLog.objects.filter(id__lte=last_id).delete()
    state.put(state.LAST_PUSH_AT, timezone.now().isoformat())
    return len(changes)


def push_all() -> int:
    total = 0
    while True:
        sent = push_once()
        if sent == 0 and not ChangeLog.objects.exists():
            return total
        total += sent


# --- Recepción (nube -> servidor local) -------------------------------------


def after_pull_hooks(changes: list[dict]) -> None:
    """Efectos locales de lo que llegó de la nube.

    Un pistoleo de faena puede llegar después de que la planta digitalizó la
    guía (la planta estuvo sin internet). La nube no enlaza el pistoleo porque
    no digitaliza; lo hace el servidor local aquí, como lo habría hecho al
    digitalizar. Corre fuera del modo "aplicando", así que el enlace vuelve a la
    nube como un cambio normal.
    """
    scan_ids = [c['id'] for c in changes if c['table'] == SiteScan._meta.db_table and c['op'] == 'U']
    if not scan_ids:
        return
    codes = set(
        SiteScan.objects.filter(pk__in=scan_ids, order__isnull=True).values_list('scanned_code', flat=True)
    )
    if not codes:
        return
    for order in LaundryOrder.objects.filter(Q(order_number__in=codes) | Q(control_code__in=codes)):
        with transaction.atomic():
            attach_pending_site_scans(order)


def pull_once() -> bool:
    """Baja y aplica un lote del feed de la nube. Devuelve True si quedan más."""
    cursor = state.get(state.PULL_CURSOR, 0)
    try:
        response = client.call('GET', '/api/sync/pull', params={'after': cursor, 'limit': PULL_BATCH})
    except client.CloudRejected as exc:
        if exc.status == 410:
            logger.warning('La nube pide volver a bajar la foto completa: %s', exc.detail)
            bootstrap(wipe=False)
            return False
        raise
    changes = response.get('changes', [])
    if changes:
        apply_changes(changes, source=SOURCE_CLOUD)
        after_pull_hooks(changes)
    state.put(state.PULL_CURSOR, response.get('last_id', cursor))
    state.put(state.LAST_PULL_AT, timezone.now().isoformat())
    return bool(response.get('has_more'))


def pull_all() -> None:
    while pull_once():
        pass


# --- Instalación inicial ----------------------------------------------------


def has_local_operation() -> bool:
    return LaundryOrder.objects.exists() or ChangeLog.objects.filter(
        table__in=['orders_laundryorder', 'weighing_weighin']
    ).exists()


def bootstrap(wipe: bool = True, force: bool = False) -> dict:
    """Baja de la nube la foto completa del catálogo y la ventana de operación.

    Con `wipe` vacía antes las tablas sincronizadas: las migraciones siembran
    filas (los roles de sistema, la configuración de pesaje) con IDs que no
    tienen por qué coincidir con los de la nube, y mezclarlas duplicaría cada
    rol. Sin `wipe` solo actualiza (se usa cuando la nube pide resincronizar).
    """
    if wipe and has_local_operation() and not force:
        raise BootstrapRefused(
            'El servidor local ya tiene guías propias. Usa --force solo si sabes que ya están en la nube.'
        )

    head = client.call('GET', '/api/sync/cursor')['last_id']
    counts: dict[str, int] = {}

    with transaction.atomic():
        if wipe:
            with triggers.applying():
                for spec in reversed(SYNCED_MODELS):
                    spec.model._base_manager.all().delete()
            ChangeLog.objects.all().delete()

        for spec in SYNCED_MODELS:
            after_id = 0
            total = 0
            while True:
                page = client.call('GET', '/api/sync/snapshot', params={
                    'table': spec.table,
                    'after_id': after_id,
                    'limit': SNAPSHOT_BATCH,
                    'retention_days': settings.SYNC_LOCAL_RETENTION_DAYS,
                })
                with triggers.applying():
                    for row in page['rows']:
                        with transaction.atomic():
                            apply_row(spec, row['id'], 'U', row['data'], SOURCE_CLOUD)
                total += len(page['rows'])
                after_id = page['last_id']
                if not page['has_more']:
                    break
            counts[spec.table] = total

        ensure_sequences()
        state.put(state.PULL_CURSOR, head)
        state.put(state.BOOTSTRAPPED_AT, timezone.now().isoformat())
    return counts


def is_bootstrapped() -> bool:
    return state.get(state.BOOTSTRAPPED_AT) is not None


# --- Estado -----------------------------------------------------------------


def status() -> dict:
    values = state.get_all()
    return {
        'node': 'edge',
        'configured': client.is_configured(),
        'cloud_url': settings.SYNC_CLOUD_URL,
        'bootstrapped': values.get(state.BOOTSTRAPPED_AT) is not None,
        'online': bool(values.get(state.ONLINE)),
        'pending_changes': pending_count(),
        'last_push_at': values.get(state.LAST_PUSH_AT),
        'last_pull_at': values.get(state.LAST_PULL_AT),
        'last_error': values.get(state.LAST_ERROR),
        'retention_days': settings.SYNC_LOCAL_RETENTION_DAYS,
        'open_issues': SyncIssue.objects.filter(resolved_at__isnull=True).count(),
        'nodes': [],
    }
