"""La ventana de datos operativos que conserva el servidor local.

El servidor local guarda completo lo que la planta necesita para funcionar
—usuarios, roles, catálogo, trabajadores, correlativos, hotelería— y solo los
últimos N días (90 por defecto) de guías y pesajes. Lo anterior sigue en la
nube, que es el respaldo, y la terminal lo consulta ahí cuando hay internet.

Una guía queda en la ventana mientras siga viva aunque sea vieja: abierta en
planta, o despachada hace poco. Solo sale la que ya terminó su ciclo.

La misma regla define tres cosas, y por eso vive en un solo lugar:
- qué baja la nube al instalar el servidor local (`cloud_snapshot_queryset`),
- qué borra el servidor local cada día (`prune_local`),
- qué filas viejas ignora el servidor local si llegan de la nube (`edge_accepts`).
"""

from datetime import datetime, timedelta

from django.conf import settings
from django.db import models, transaction
from django.db.models import Q
from django.utils import timezone

from orders.models import LaundryOrder, OrderStatus, SiteScan
from sync import triggers
from sync.models import ChangeLog
from sync.registry import SyncedModel
from weighing.models import WeighIn

OPEN_STATUSES = (
    OrderStatus.RECEIVED,
    OrderStatus.QUALITY_CHECK,
    OrderStatus.INCOMPLETE,
    OrderStatus.COMPLETED,
)


def cutoff(retention_days: int | None = None) -> datetime:
    days = retention_days if retention_days is not None else settings.SYNC_LOCAL_RETENTION_DAYS
    return timezone.now() - timedelta(days=days)


def local_orders_q(limit: datetime) -> Q:
    return (
        Q(status__in=OPEN_STATUSES)
        | Q(received_at__gte=limit)
        | Q(status=OrderStatus.DISPATCHED, dispatched_at__gte=limit)
    )


def local_weigh_ins_q(limit: datetime, orders: models.QuerySet) -> Q:
    return Q(status=WeighIn.Status.PENDING) | Q(weighed_at__gte=limit) | Q(order__in=orders)


def order_in_window(values: dict, limit: datetime) -> bool:
    status = values.get('status')
    if status in OPEN_STATUSES:
        return True
    received_at = values.get('received_at')
    if received_at is not None and received_at >= limit:
        return True
    dispatched_at = values.get('dispatched_at')
    return status == OrderStatus.DISPATCHED and dispatched_at is not None and dispatched_at >= limit


# Padre en la ventana del que depende cada tabla hija: si el padre no está en
# el servidor local, la hija tampoco tiene por qué estar.
WINDOW_PARENTS: dict[str, tuple[str, type[models.Model]]] = {
    'orders.OrderItem': ('order_id', LaundryOrder),
    'orders.MissingItemResolution': ('order_id', LaundryOrder),
    'orders.OrderStatusHistory': ('order_id', LaundryOrder),
    'orders.SyncConflict': ('order_id', LaundryOrder),
    'weighing.WeighLabel': ('weigh_in_id', WeighIn),
}


def edge_accepts(spec: SyncedModel, values: dict) -> bool:
    """En el servidor local, si una fila que no tiene y llega de la nube le corresponde guardarla."""
    if not spec.windowed:
        return True
    limit = cutoff()
    if spec.label == 'orders.LaundryOrder':
        return order_in_window(values, limit)
    if spec.label == 'weighing.WeighIn':
        if values.get('status') == WeighIn.Status.PENDING:
            return True
        weighed_at = values.get('weighed_at')
        if weighed_at is not None and weighed_at >= limit:
            return True
        order_id = values.get('order_id')
        return order_id is not None and LaundryOrder.objects.filter(pk=order_id).exists()
    if spec.label == 'orders.SiteScan':
        order_id = values.get('order_id')
        if order_id is not None:
            return LaundryOrder.objects.filter(pk=order_id).exists()
        scanned_at = values.get('scanned_at')
        return scanned_at is not None and scanned_at >= limit
    parent = WINDOW_PARENTS.get(spec.label)
    if parent is not None:
        attname, parent_model = parent
        parent_id = values.get(attname)
        return parent_id is None or parent_model.objects.filter(pk=parent_id).exists()
    return True


def cloud_snapshot_queryset(spec: SyncedModel, retention_days: int) -> models.QuerySet:
    """Filas de una tabla que la nube entrega al instalar un servidor local."""
    queryset = spec.model._base_manager.all()
    if spec.exclude_filter:
        queryset = queryset.exclude(**spec.exclude_filter)
    if not spec.windowed:
        return queryset
    limit = cutoff(retention_days)
    orders = LaundryOrder.objects.filter(local_orders_q(limit)).values('id')
    weigh_ins = WeighIn.objects.filter(local_weigh_ins_q(limit, orders)).values('id')
    if spec.label == 'orders.LaundryOrder':
        return queryset.filter(id__in=orders)
    if spec.label == 'weighing.WeighIn':
        return queryset.filter(id__in=weigh_ins)
    if spec.label == 'weighing.WeighLabel':
        return queryset.filter(weigh_in_id__in=weigh_ins)
    if spec.label == 'orders.SiteScan':
        return queryset.filter(Q(order_id__in=orders) | Q(order__isnull=True, scanned_at__gte=limit))
    return queryset.filter(order_id__in=orders)


def prune_local() -> dict:
    """Borra del servidor local lo que salió de la ventana. Sigue en la nube.

    Solo corre con el outbox vacío: si algo todavía no llegó a la nube, borrarlo
    aquí sería perderlo. Los borrados no se anotan en el changelog (nada de
    esto debe borrarse en la nube).
    """
    if ChangeLog.objects.exists():
        return {'skipped': 'Hay cambios pendientes de enviar a la nube.'}

    limit = cutoff()
    with transaction.atomic(), triggers.applying():
        old_orders = LaundryOrder.objects.exclude(local_orders_q(limit))
        weigh_ins_deleted, _ = WeighIn.objects.filter(order__in=old_orders).delete()
        orders_deleted, _ = old_orders.delete()
        kept_orders = LaundryOrder.objects.values('id')
        stale_weigh_ins, _ = WeighIn.objects.exclude(local_weigh_ins_q(limit, kept_orders)).delete()
        scans_deleted, _ = SiteScan.objects.filter(order__isnull=True, scanned_at__lt=limit).delete()
    return {
        'orders': orders_deleted,
        'weigh_ins': weigh_ins_deleted + stale_weigh_ins,
        'orphan_scans': scans_deleted,
    }
