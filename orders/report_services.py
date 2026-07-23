"""Lógica de agregación de la reportería en tiempo real (Dashboards 1 y 3).

Funciones puras: reciben filtros, consultan el ORM de forma agregada y devuelven
dicts o querysets listos para serializar. No conocen el request ni la respuesta
HTTP (eso vive en `report_api.py`). Respuesta siempre agregada: el histórico
ronda las 280.000 guías, así que ningún endpoint devuelve la tabla completa.

Contrato: ai_context/REPORTES_API_CONTRACT.md.
"""

from datetime import date, timedelta

from django.core.cache import cache
from django.db.models import Avg, Count, F, Q, QuerySet, Sum
from django.db.models.functions import Coalesce, Now, TruncDate, TruncWeek
from django.utils import timezone

# El resumen operativo se pollea cada 15-30 s y cuesta ~250 ms sobre las ~283k
# guías; un micro-caché de 10 s corta la carga repetida (varios admins mirando a
# la vez) sin que la frescura se note para "tiempo real". El resto de endpoints
# es lo bastante barato (<80 ms) como para no necesitar caché.
OPERATIONS_SUMMARY_TTL = 10

from orders.models import LaundryOrder, MissingItemResolution, OrderStatus

# Estados que representan trabajo en curso (WIP): la guía todavía no se entrega ni
# se cobra, así que puede estar "atascada".
WIP_STATUSES = (
    OrderStatus.RECEIVED,
    OrderStatus.QUALITY_CHECK,
    OrderStatus.INCOMPLETE,
    OrderStatus.COMPLETED,
)

# Horas máximas que una guía puede pasar en cada estado antes de considerarse
# atascada. Valores por defecto; ajustables sin tocar la lógica.
STALL_THRESHOLDS_H = {
    OrderStatus.RECEIVED: 72,
    OrderStatus.QUALITY_CHECK: 48,
    OrderStatus.INCOMPLETE: 48,
    OrderStatus.COMPLETED: 96,
}

# Timestamp de entrada a cada estado. No existe un `en_revision_at`, así que
# EN_REVISION usa `updated_at` como proxy (estado transitorio y auto-decidido).
STALL_TIMESTAMP = {
    OrderStatus.RECEIVED: 'received_at',
    OrderStatus.QUALITY_CHECK: 'updated_at',
    OrderStatus.INCOMPLETE: 'incomplete_at',
    OrderStatus.COMPLETED: 'completed_at',
}


def apply_filters(
    qs: QuerySet,
    *,
    company_id: int | None = None,
    client_id: int | None = None,
    worker_id: int | None = None,
    date_from: date | None = None,
    date_to: date | None = None,
    delivery_flow: str | None = None,
    date_field: str = 'received_at',
) -> QuerySet:
    """Aplica los filtros comunes de la reportería (cliente/empresa como dimensiones).

    `client_id` agrega todas las empresas del cliente; `company_id` baja a una
    empresa concreta (en el caso cliente=empresa 1:1 dan lo mismo). La faena no se
    filtra: no es una entidad del modelo (solo existe como `SiteScan` y
    `Company.reference_prefix`). El `delivery_flow` vive en `Company`, así que se
    filtra por la relación.
    """
    if company_id:
        qs = qs.filter(company_id=company_id)
    if client_id:
        qs = qs.filter(company__client_id=client_id)
    if worker_id:
        qs = qs.filter(worker_id=worker_id)
    if delivery_flow:
        qs = qs.filter(company__delivery_flow=delivery_flow)
    if date_from:
        qs = qs.filter(**{f'{date_field}__date__gte': date_from})
    if date_to:
        qs = qs.filter(**{f'{date_field}__date__lte': date_to})
    return qs


def _stalled_q() -> Q:
    """OR de (estado X AND su timestamp de entrada < ahora - umbral_X)."""
    now = timezone.now()
    q = Q()
    for status, hours in STALL_THRESHOLDS_H.items():
        field = STALL_TIMESTAMP[status]
        q |= Q(**{'status': status, f'{field}__lt': now - timedelta(hours=hours)})
    return q


# --- Dashboard 1: Torre de Control Operacional -----------------------------

def get_operations_summary(**filters) -> dict:
    """Tarjetas KPI + embudo de estados. Endpoint de polling (15-30 s), cacheado 10 s."""
    # Clave por combinación de filtros para no mezclar vistas de distintas empresas.
    key = 'ops_summary:' + ':'.join(f'{k}={filters.get(k)}' for k in sorted(filters))
    cached = cache.get(key)
    if cached is not None:
        return cached
    result = _compute_operations_summary(**filters)
    cache.set(key, result, OPERATIONS_SUMMARY_TTL)
    return result


def _compute_operations_summary(**filters) -> dict:
    now = timezone.now()
    qs = apply_filters(LaundryOrder.objects.all(), **filters)

    counts = dict(
        qs.values_list('status').annotate(c=Count('id')).values_list('status', 'c')
    )
    wip = qs.filter(status__in=WIP_STATUSES)

    at_risk = (
        wip.exclude(status=OrderStatus.COMPLETED)
        .filter(promised_at__lt=now + timedelta(hours=24))
        .count()
    )
    avg_age = wip.aggregate(d=Avg(Now() - F('received_at')))['d']

    return {
        'generated_at': now,
        'wip_total': sum(counts.get(s, 0) for s in WIP_STATUSES),
        'by_status': [
            {'status': s, 'count': counts.get(s, 0)} for s in OrderStatus.values
        ],
        'stalled_count': wip.filter(_stalled_q()).count(),
        'at_risk_count': at_risk,
        'avg_wip_age_days': round(avg_age.total_seconds() / 86400, 1) if avg_age else 0.0,
        'received_today': qs.filter(received_at__date=now.date()).count(),
        'delivered_today': qs.filter(delivered_at__date=now.date()).count(),
    }


def get_stalled_orders(**filters) -> QuerySet:
    """Guías atascadas para la tabla de excepciones. Paginada en el router."""
    qs = apply_filters(LaundryOrder.objects.all(), **filters).filter(status__in=WIP_STATUSES)
    return (
        qs.filter(_stalled_q())
        .select_related('company', 'worker')
        .order_by('received_at')  # más antiguas primero
    )


def get_timeseries(granularity: str = 'day', **filters) -> dict:
    """Serie diaria (o semanal) de guías ingresadas vs. entregadas.

    Son dos agregaciones sobre columnas de fecha distintas (`received_at` y
    `delivered_at`) que se fusionan por fecha; no se puede hacer con un solo
    GROUP BY.
    """
    trunc = TruncWeek if granularity == 'week' else TruncDate

    received_qs = apply_filters(LaundryOrder.objects.all(), date_field='received_at', **filters)
    received = {
        row['bucket']: row['c']
        for row in received_qs.annotate(bucket=trunc('received_at'))
        .values('bucket')
        .annotate(c=Count('id'))
        if row['bucket'] is not None
    }

    delivered_qs = apply_filters(
        LaundryOrder.objects.filter(delivered_at__isnull=False),
        date_field='delivered_at',
        **filters,
    )
    delivered = {
        row['bucket']: row['c']
        for row in delivered_qs.annotate(bucket=trunc('delivered_at'))
        .values('bucket')
        .annotate(c=Count('id'))
        if row['bucket'] is not None
    }

    buckets = sorted(set(received) | set(delivered))
    return {
        'granularity': granularity,
        'points': [
            {
                'date': b.isoformat(),
                'received': received.get(b, 0),
                'delivered': delivered.get(b, 0),
            }
            for b in buckets
        ],
    }


# --- Dashboard 3: Calidad e Incidencias ------------------------------------

def get_quality_summary(**filters) -> dict:
    """KPIs de morrales incompletos: tasa, resolución y costo de reposición."""
    now = timezone.now()

    # Denominador: guías digitalizadas del período (por received_at).
    orders = apply_filters(LaundryOrder.objects.all(), **filters)
    total = orders.count()

    # Incidencias del período: las que se volvieron incompletas, fechadas por
    # incomplete_at (permite medir tasa y tiempos de resolución).
    incident_filters = {**filters, 'date_field': 'incomplete_at'}
    incidents = apply_filters(
        LaundryOrder.objects.filter(incomplete_at__isnull=False), **incident_filters
    )
    incomplete = incidents.count()

    # "Incompletas abiertas" es una foto puntual (¿cuántas están atascadas AHORA?),
    # no una métrica de período: se cuenta por estado y sin filtro de fecha, para
    # que no dependa de si el legado rellenó incomplete_at. Se conservan los
    # filtros de empresa/trabajador/flujo.
    snapshot_filters = {**filters, 'date_from': None, 'date_to': None}
    open_incomplete = apply_filters(
        LaundryOrder.objects.filter(status=OrderStatus.INCOMPLETE), **snapshot_filters
    ).count()

    resolutions = MissingItemResolution.objects.filter(order__in=incidents)
    mix = dict(
        resolutions.values_list('resolution_type')
        .annotate(c=Count('id'))
        .values_list('resolution_type', 'c')
    )
    purchase_cost = (
        resolutions.filter(resolution_type=MissingItemResolution.ResolutionType.PURCHASED)
        .aggregate(s=Sum('purchase_cost'))['s']
        or 0
    )
    avg_res = incidents.filter(completed_at__isnull=False).aggregate(
        d=Avg(F('completed_at') - F('incomplete_at'))
    )['d']

    return {
        'generated_at': now,
        'total_orders': total,
        'incomplete_orders': incomplete,
        'incomplete_rate': round(incomplete / total, 4) if total else 0.0,
        'open_incomplete': open_incomplete,
        'discrepancy_rate': round(orders.exclude(observations='').count() / total, 4) if total else 0.0,
        'resolution_mix': {
            'encontrada': mix.get(MissingItemResolution.ResolutionType.FOUND, 0),
            'comprada': mix.get(MissingItemResolution.ResolutionType.PURCHASED, 0),
        },
        'purchase_cost_total': float(purchase_cost),
        'avg_resolution_hours': round(avg_res.total_seconds() / 3600, 1) if avg_res else 0.0,
    }


def get_garment_pareto(**filters) -> dict:
    """Pareto de prendas más problemáticas (incidencias por tipo de prenda)."""
    incident_filters = {**filters, 'date_field': 'incomplete_at'}
    incidents = apply_filters(
        LaundryOrder.objects.filter(incomplete_at__isnull=False), **incident_filters
    )

    rows = list(
        MissingItemResolution.objects.filter(order__in=incidents)
        # garment_type__name es NULL para prendas fuera de catálogo; en ese caso
        # cae al custom_name del item.
        .annotate(name=Coalesce('item__garment_type__name', 'item__custom_name'))
        .values('name')
        .annotate(incident_count=Count('id'))
        .order_by('-incident_count')
    )

    total = sum(r['incident_count'] for r in rows)
    result_rows = []
    running = 0
    for r in rows:
        running += r['incident_count']
        result_rows.append(
            {
                'name': r['name'] or '(sin nombre)',
                'incident_count': r['incident_count'],
                'cumulative_pct': round(running / total * 100, 1) if total else 0.0,
            }
        )

    return {'total_incidents': total, 'rows': result_rows}


def get_incidents(resolution_type: str | None = None, **filters) -> QuerySet:
    """Tabla detallada de incidencias. Paginada en el router.

    `resolution_type`: 'ENCONTRADA' | 'COMPRADA' filtran por el tipo de resolución
    registrada; 'OPEN' deja solo las que siguen en estado INCOMPLETA.
    """
    incident_filters = {**filters, 'date_field': 'incomplete_at'}
    qs = apply_filters(
        LaundryOrder.objects.filter(incomplete_at__isnull=False), **incident_filters
    )

    if resolution_type == 'OPEN':
        qs = qs.filter(status=OrderStatus.INCOMPLETE)
    elif resolution_type in (
        MissingItemResolution.ResolutionType.FOUND,
        MissingItemResolution.ResolutionType.PURCHASED,
    ):
        qs = qs.filter(missing_item_resolutions__resolution_type=resolution_type).distinct()

    return (
        qs.select_related('company', 'worker')
        .prefetch_related(
            'missing_item_resolutions',
            'missing_item_resolutions__item',
            'missing_item_resolutions__item__garment_type',
        )
        .order_by('-incomplete_at')
    )
