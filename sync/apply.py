"""Aplica en este nodo los cambios que llegan del otro.

Todas las reglas son deterministas y simétricas: la nube y el servidor local
toman la misma decisión ante el mismo par de versiones, sin importar en qué
orden les llegaron. Eso es lo que garantiza que terminen iguales aunque hayan
pasado una semana sin verse.
"""

import logging
from dataclasses import dataclass, field

from django.db import IntegrityError, models, transaction
from django.utils import timezone

from common.node import is_edge
from orders.models import OrderStatus
from sync import triggers
from sync.models import SyncIssue
from sync.registry import LWW, MERGE, ORDER, SyncedModel, get_spec, order_index
from sync.serialization import deserialize
from sync.window import edge_accepts

logger = logging.getLogger(__name__)

SOURCE_EDGE = 'edge'
SOURCE_CLOUD = 'cloud'

# El estado de una guía solo avanza. Si un lado la vio ENTREGADA y el otro
# DESPACHADA, es ENTREGADA: nadie la devuelve a planta.
STATUS_RANK = {
    OrderStatus.RECEIVED: 0,
    OrderStatus.QUALITY_CHECK: 1,
    OrderStatus.INCOMPLETE: 2,
    OrderStatus.COMPLETED: 3,
    OrderStatus.DISPATCHED: 4,
    OrderStatus.DELIVERED: 5,
}

# Hitos de la guía: una vez registrados en cualquiera de los dos lados, no se
# pierden. Los de faena (recepción, entrega) los escribe la nube vía app
# móvil; los de planta, el servidor local.
ORDER_MILESTONES = (
    'site_received_at', 'site_received_by_id',
    'laundry_received_at',
    'packed_at', 'packed_by_id',
    'incomplete_at',
    'completed_at',
    'dispatched_at', 'dispatched_by_id',
    'delivered_at', 'delivered_by_id',
    'reviewed_by_id',
)


@dataclass
class ApplyResult:
    applied: int = 0
    skipped: int = 0
    issues: list[str] = field(default_factory=list)


def _record(kind: str, spec: SyncedModel, row_id: int, detail: str, payload: dict | None = None) -> None:
    SyncIssue.objects.create(kind=kind, table=spec.table, row_id=row_id, detail=detail[:255], payload=payload)


def merge_order(existing: models.Model, incoming: dict, source: str) -> dict:
    """Fusiona dos versiones de una guía.

    La base es siempre la versión de la planta (la del servidor local): es la
    dueña de todo lo que ocurre en Antofagasta. Sobre esa base, los hitos
    vacíos se completan con los del otro lado y el estado queda en el más
    avanzado de los dos.
    """
    current = {f.attname: getattr(existing, f.attname) for f in existing._meta.concrete_fields}
    edge_version, other = (incoming, current) if source == SOURCE_EDGE else (current, incoming)
    merged = {**current, **edge_version} if source == SOURCE_EDGE else dict(current)

    for attname in ORDER_MILESTONES:
        if merged.get(attname) is None and other.get(attname) is not None:
            merged[attname] = other[attname]

    statuses = [s for s in (current.get('status'), incoming.get('status')) if s]
    if statuses:
        merged['status'] = max(statuses, key=lambda s: STATUS_RANK.get(s, -1))

    stamps = [s for s in (current.get('updated_at'), incoming.get('updated_at')) if s]
    if stamps:
        merged['updated_at'] = max(stamps)
    return merged


def merge_nonnull(existing: models.Model, incoming: dict) -> dict:
    merged = dict(incoming)
    for attname, value in incoming.items():
        if value is None and getattr(existing, attname, None) is not None:
            merged[attname] = getattr(existing, attname)
    return merged


def _suffixed(value: str, row_id: int, max_length: int | None) -> str:
    suffix = f'~{str(row_id)[-4:]}'
    if max_length:
        value = value[: max_length - len(suffix)]
    return f'{value}{suffix}'


def resolve_unique(spec: SyncedModel, values: dict, row_id: int) -> bool:
    """Resuelve el choque de dos filas distintas con la misma clave única.

    Pasa cuando los dos nodos crearon lo mismo sin conexión (el usuario "juan",
    la empresa "Orica"). Pierde siempre la fila de ID mayor —la del servidor
    local, que numera por encima de la nube—, en los dos nodos por igual:
    se le agrega un sufijo al nombre (`juan~0042`), o se descarta si es un
    registro que nadie referencia (un precio duplicado).

    Devuelve False si la fila entrante no debe escribirse.
    """
    manager = spec.model._base_manager
    for key in spec.unique_keys:
        lookup = {attname: values.get(attname) for attname in key}
        if any(value in (None, '') for value in lookup.values()):
            continue
        rival = manager.filter(**lookup).exclude(pk=row_id).first()
        if rival is None:
            continue

        if spec.rename_field is None:
            if row_id > rival.pk:
                _record(SyncIssue.Kind.DUPLICATE, spec, row_id,
                        f'Duplicado de #{rival.pk} ({lookup}); se conservó el de menor ID.')
                return False
            rival.delete()
            _record(SyncIssue.Kind.DUPLICATE, spec, rival.pk,
                    f'Duplicado de #{row_id} ({lookup}); se conservó el de menor ID.')
            continue

        field_obj = spec.model._meta.get_field(spec.rename_field)
        if row_id > rival.pk:
            original = values[spec.rename_field]
            values[spec.rename_field] = _suffixed(original, row_id, field_obj.max_length)
            _record(SyncIssue.Kind.RENAMED, spec, row_id,
                    f'"{original}" ya existía (#{rival.pk}); quedó como "{values[spec.rename_field]}".')
        else:
            original = getattr(rival, spec.rename_field)
            renamed = _suffixed(original, rival.pk, field_obj.max_length)
            manager.filter(pk=rival.pk).update(**{spec.rename_field: renamed})
            _record(SyncIssue.Kind.RENAMED, spec, rival.pk,
                    f'"{original}" ya existía (#{row_id}); quedó como "{renamed}".')
    return True


def apply_row(spec: SyncedModel, row_id: int, op: str, data: dict | None, source: str) -> bool:
    """Aplica una fila. Devuelve False si se omitió a propósito."""
    model = spec.model
    manager = model._base_manager

    if op == 'D':
        manager.filter(pk=row_id).delete()
        return True

    values = deserialize(spec, data or {})
    values['id'] = row_id
    existing = manager.filter(pk=row_id).first()

    if existing is None and is_edge() and not edge_accepts(spec, values):
        return False

    if existing is not None:
        if spec.policy == LWW:
            incoming_at, current_at = values.get('updated_at'), getattr(existing, 'updated_at', None)
            if incoming_at is not None and current_at is not None and incoming_at < current_at:
                _record(SyncIssue.Kind.DISCARDED, spec, row_id,
                        'Se editó en los dos lados; ganó la edición más reciente.', data)
                return False
        elif spec.policy == ORDER:
            values = merge_order(existing, values, source)
        elif spec.policy == MERGE:
            values = merge_nonnull(existing, values)

    # Un nulo en una columna obligatoria no es un dato: viene de un nodo con
    # otra versión del esquema. Se conserva lo existente (o el valor por
    # defecto), y las marcas automáticas se completan con la hora actual.
    for field_obj in model._meta.concrete_fields:
        if field_obj.null or values.get(field_obj.attname, ...) is not None:
            continue
        if getattr(field_obj, 'auto_now', False) or getattr(field_obj, 'auto_now_add', False):
            values[field_obj.attname] = getattr(existing, field_obj.attname, None) or timezone.now()
        else:
            values.pop(field_obj.attname, None)

    if not resolve_unique(spec, values, row_id):
        return False

    instance = existing if existing is not None else model()
    for attname, value in values.items():
        setattr(instance, attname, value)
    # raw=True: guarda los valores tal cual (no pisa `updated_at` con la hora
    # de aplicación ni dispara la lógica de guardado de la app). Es lo mismo que
    # usa `loaddata`.
    instance.save_base(raw=True, force_insert=existing is None, force_update=existing is not None)
    return True


def sort_changes(changes: list[dict]) -> list[dict]:
    """Altas antes que sus dependientes; borrados después y al revés."""
    index = order_index()
    upserts = [c for c in changes if c['op'] != 'D']
    deletes = [c for c in changes if c['op'] == 'D']
    upserts.sort(key=lambda c: (index.get(c['table'], 999), c['id']))
    deletes.sort(key=lambda c: (-index.get(c['table'], -1), c['id']))
    return upserts + deletes


def apply_changes(changes: list[dict], source: str) -> ApplyResult:
    """Aplica un lote en una transacción. Una fila que falla no detiene al resto."""
    result = ApplyResult()
    with transaction.atomic(), triggers.applying():
        for change in sort_changes(changes):
            spec = get_spec(change['table'])
            try:
                with transaction.atomic():
                    applied = apply_row(spec, change['id'], change['op'], change.get('data'), source)
            except (IntegrityError, ValueError, TypeError, models.ObjectDoesNotExist) as exc:
                logger.exception('No se pudo aplicar %s#%s', change['table'], change['id'])
                _record(SyncIssue.Kind.ERROR, spec, change['id'], str(exc), change.get('data'))
                result.issues.append(f'{change["table"]}#{change["id"]}: {exc}')
                continue
            if applied:
                result.applied += 1
            else:
                result.skipped += 1
    return result
