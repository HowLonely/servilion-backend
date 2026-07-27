"""Lógica de agregación de la reportería en tiempo real (Dashboards 1 y 3).

Funciones puras: reciben filtros, consultan el ORM de forma agregada y devuelven
dicts o querysets listos para serializar. No conocen el request ni la respuesta
HTTP (eso vive en `report_api.py`). Respuesta siempre agregada: el histórico
ronda las 280.000 guías, así que ningún endpoint devuelve la tabla completa.

Contrato: ai_context/REPORTES_API_CONTRACT.md.
"""

from datetime import date, timedelta

from django.core.cache import cache
from django.db.models import Avg, Case, Count, F, Q, QuerySet, Sum, When
from django.db.models.functions import Coalesce, TruncDate, TruncWeek
from django.utils import timezone

# El resumen operativo se pollea cada 15-30 s y cuesta ~250 ms sobre las ~283k
# guías; un micro-caché de 10 s corta la carga repetida (varios admins mirando a
# la vez) sin que la frescura se note para "tiempo real". El resto de endpoints
# es lo bastante barato (<80 ms) como para no necesitar caché.
OPERATIONS_SUMMARY_TTL = 10

from orders.models import LaundryOrder, MissingItemResolution, OrderStatus

# Estados de trabajo ACTIVO en planta (WIP real): la guía todavía se está
# procesando y por tanto puede atascarse. COMPLETADA queda fuera a propósito:
# significa "producida y despachada" (ya salió de planta), no trabajo pendiente.
# Incluirla inflaba el WIP con las ~210k guías históricas ya terminadas — el bug
# que hacía ver 210k "en proceso" y "atascadas". La entrega (COMPLETADA ->
# ENTREGADA) se registra aparte y hoy casi no se puebla, así que tampoco cuenta
# como atasco de planta.
IN_PLANT_STATUSES = (
    OrderStatus.RECEIVED,
    OrderStatus.QUALITY_CHECK,
    OrderStatus.INCOMPLETE,
)

# Turnaround objetivo (días recepción -> producción): la meta de servicio contra
# la que se mide el % "a tiempo". La mediana real ronda 5 días; ajustable aquí.
TAT_TARGET_DAYS = 5

# Tramos de antigüedad (días) para desglosar el WIP en planta. Límite superior
# None = "y más". Reemplaza a la antigüedad media (dominada por outliers del
# archivo) por una distribución accionable.
AGING_BUCKETS_DAYS = ((0, 1), (1, 2), (2, 4), (4, 7), (7, None))

# Horas máximas que una guía puede pasar en cada estado de planta antes de
# considerarse atascada. Valores por defecto; ajustables sin tocar la lógica.
STALL_THRESHOLDS_H = {
    OrderStatus.RECEIVED: 72,
    OrderStatus.QUALITY_CHECK: 48,
    OrderStatus.INCOMPLETE: 48,
}

# Timestamp de entrada a cada estado de planta. No existe un `en_revision_at`,
# así que EN_REVISION usa `updated_at` como proxy (estado transitorio y
# auto-decidido).
STALL_TIMESTAMP = {
    OrderStatus.RECEIVED: 'received_at',
    OrderStatus.QUALITY_CHECK: 'updated_at',
    OrderStatus.INCOMPLETE: 'incomplete_at',
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


def annotate_state_since(qs: QuerySet) -> QuerySet:
    """Anota `state_since`: el instante en que la guía entró a su estado actual.

    Usa el timestamp de entrada de cada estado (STALL_TIMESTAMP) con fallback a
    `received_at` cuando no se registró — el legado dejó `incomplete_at` nulo en
    muchas guías INCOMPLETA, y sin fallback quedaban fuera del aging y del atasco.
    """
    return qs.annotate(
        state_since=Case(
            When(status=OrderStatus.RECEIVED, then=F('received_at')),
            When(status=OrderStatus.QUALITY_CHECK, then=Coalesce('updated_at', 'received_at')),
            When(status=OrderStatus.INCOMPLETE, then=Coalesce('incomplete_at', 'received_at')),
            default=F('received_at'),
        )
    )


def _stalled_q(now) -> Q:
    """OR de (estado X AND state_since < ahora - umbral_X). Requiere `state_since`."""
    q = Q()
    for status, hours in STALL_THRESHOLDS_H.items():
        q |= Q(status=status, state_since__lt=now - timedelta(hours=hours))
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


def _effective_period(filters: dict, now) -> tuple:
    """Ventana de flujo (inicio, fin, días) + la ventana previa del mismo largo.

    Sin rango explícito usa los últimos 30 días terminando hoy. La ventana previa
    alimenta las flechas de tendencia (Δ% vs período anterior comparable).
    """
    end = filters.get('date_to') or now.date()
    start = filters.get('date_from') or (end - timedelta(days=29))
    days = (end - start).days + 1
    prev_end = start - timedelta(days=1)
    prev_start = prev_end - timedelta(days=days - 1)
    return start, end, days, prev_start, prev_end


def _delta_pct(cur: int, prev: int) -> float | None:
    """Variación porcentual vs período anterior; None si no hay base de comparación."""
    if not prev:
        return None
    return round((cur - prev) / prev * 100, 1)


def _percentile(sorted_vals: list, p: float) -> float:
    """Percentil por interpolación lineal. `sorted_vals` ya ordenado ascendente."""
    n = len(sorted_vals)
    if n == 0:
        return 0.0
    if n == 1:
        return sorted_vals[0]
    idx = p * (n - 1)
    lo = int(idx)
    hi = min(lo + 1, n - 1)
    return sorted_vals[lo] + (sorted_vals[hi] - sorted_vals[lo]) * (idx - lo)


def _bucketize_aging(values_days: list) -> list:
    """Reparte antigüedades (en días) en los tramos de AGING_BUCKETS_DAYS."""
    out = []
    for lo, hi in AGING_BUCKETS_DAYS:
        if hi is None:
            count = sum(1 for v in values_days if v >= lo)
            label = f'{lo}d+'
        else:
            count = sum(1 for v in values_days if lo <= v < hi)
            label = f'{lo}-{hi}d'
        out.append({'label': label, 'count': count})
    return out


def _compute_operations_summary(**filters) -> dict:
    now = timezone.now()
    start, end, period_days, prev_start, prev_end = _effective_period(filters, now)

    # Dimensiones (empresa/cliente/trabajador/flujo) sin la ventana de fecha: la
    # foto de planta ("ahora") no depende del rango elegido para el flujo.
    dim = {k: filters.get(k) for k in ('company_id', 'client_id', 'worker_id', 'delivery_flow')}

    # --- Flujo del período: ingreso (received_at) vs producción (completed_at) ---
    def _count(field: str, d0, d1) -> int:
        return apply_filters(
            LaundryOrder.objects.all(), date_from=d0, date_to=d1, date_field=field, **dim
        ).count()

    received = _count('received_at', start, end)
    produced = _count('completed_at', start, end)
    received_prev = _count('received_at', prev_start, prev_end)
    produced_prev = _count('completed_at', prev_start, prev_end)

    # Incompletos surgidos en la ventana (por incomplete_at): calidad del flujo.
    incomplete = apply_filters(
        LaundryOrder.objects.filter(incomplete_at__isnull=False),
        date_from=start, date_to=end, date_field='incomplete_at', **dim,
    ).count()

    # --- Turnaround recepción -> producción de las guías producidas en la ventana.
    # Se traen las duraciones (acotadas por el período) y se percentilan en Python:
    # es más portable que PERCENTILE_CONT y el volumen por ventana es pequeño.
    tat_days = sorted(
        (c - r).total_seconds() / 86400
        for r, c in apply_filters(
            LaundryOrder.objects.filter(completed_at__isnull=False),
            date_from=start, date_to=end, date_field='completed_at', **dim,
        )
        .filter(completed_at__gte=F('received_at'))
        .values_list('received_at', 'completed_at')
    )
    on_target = sum(1 for d in tat_days if d <= TAT_TARGET_DAYS)

    # --- Foto de planta AHORA (snapshot, sin ventana de fecha) ---
    in_plant_qs = annotate_state_since(
        apply_filters(LaundryOrder.objects.filter(status__in=IN_PLANT_STATUSES), **dim)
    )
    in_plant_counts = dict(
        in_plant_qs.values_list('status').annotate(c=Count('id')).values_list('status', 'c')
    )
    stalled = in_plant_qs.filter(_stalled_q(now)).count()

    # Antigüedad de cada guía en planta desde que entró a su estado actual.
    aging_days = [
        (now - since).total_seconds() / 86400
        for since in in_plant_qs.values_list('state_since', flat=True)
        if since is not None
    ]

    # Distribución completa por estado (orienta el volumen total del sistema).
    all_counts = dict(
        apply_filters(LaundryOrder.objects.all(), **dim)
        .values_list('status')
        .annotate(c=Count('id'))
        .values_list('status', 'c')
    )

    return {
        'generated_at': now,
        'period_days': period_days,
        # Flujo del período
        'received': received,
        'produced': produced,
        'received_delta_pct': _delta_pct(received, received_prev),
        'produced_delta_pct': _delta_pct(produced, produced_prev),
        'incomplete': incomplete,
        'incomplete_rate': round(incomplete / received, 4) if received else 0.0,
        # Turnaround recepción -> producción
        'tat_p50_days': round(_percentile(tat_days, 0.5), 1),
        'tat_p90_days': round(_percentile(tat_days, 0.9), 1),
        'tat_target_days': TAT_TARGET_DAYS,
        'tat_on_target_pct': round(on_target / len(tat_days) * 100, 1) if tat_days else 0.0,
        # Foto de planta (ahora)
        'in_plant': sum(in_plant_counts.values()),
        'in_plant_by_status': [
            {'status': s, 'count': in_plant_counts.get(s, 0)} for s in IN_PLANT_STATUSES
        ],
        'open_incomplete': in_plant_counts.get(OrderStatus.INCOMPLETE, 0),
        'stalled_count': stalled,
        'oldest_in_plant_days': round(max(aging_days), 1) if aging_days else 0.0,
        'aging': _bucketize_aging(aging_days),
        'by_status': [{'status': s, 'count': all_counts.get(s, 0)} for s in OrderStatus.values],
    }


def get_stalled_orders(**filters) -> QuerySet:
    """Guías atascadas en planta para la tabla de excepciones. Paginada en el router.

    Solo trabajo activo (IN_PLANT_STATUSES): una COMPLETADA ya salió de planta, no
    se "atasca". Así la tabla queda accionable (decenas), no inundada por el
    archivo histórico.
    """
    qs = annotate_state_since(
        apply_filters(LaundryOrder.objects.all(), **filters).filter(status__in=IN_PLANT_STATUSES)
    )
    return (
        qs.filter(_stalled_q(timezone.now()))
        .select_related('company', 'worker')
        .order_by('state_since')  # las más atascadas primero
    )


def get_timeseries(granularity: str = 'day', **filters) -> dict:
    """Serie diaria (o semanal) de ingreso vs. producción vs. entrega.

    Tres agregaciones sobre columnas de fecha distintas (`received_at`,
    `completed_at`, `delivered_at`) fusionadas por fecha: no se puede en un solo
    GROUP BY. `produced` (completed_at) es la tasa real de salida de planta y el
    contrapeso natural del ingreso; `delivered` se mantiene aunque hoy casi no se
    puebla (la entrega no se registra operativamente todavía).
    """
    trunc = TruncWeek if granularity == 'week' else TruncDate

    def _series(base_qs: QuerySet, field: str) -> dict:
        qs = apply_filters(base_qs, date_field=field, **filters)
        return {
            row['bucket']: row['c']
            for row in qs.annotate(bucket=trunc(field)).values('bucket').annotate(c=Count('id'))
            if row['bucket'] is not None
        }

    received = _series(LaundryOrder.objects.all(), 'received_at')
    produced = _series(LaundryOrder.objects.filter(completed_at__isnull=False), 'completed_at')
    delivered = _series(LaundryOrder.objects.filter(delivered_at__isnull=False), 'delivered_at')

    buckets = sorted(set(received) | set(produced) | set(delivered))
    return {
        'granularity': granularity,
        'points': [
            {
                'date': b.isoformat(),
                'received': received.get(b, 0),
                'produced': produced.get(b, 0),
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
