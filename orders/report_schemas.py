"""Schemas de salida de la reportería en tiempo real (Dashboards 1 y 3).

Solo salida (`Out`): estos endpoints son de lectura agregada. Los filtros viajan
como query params planos en `report_api.py`, igual que en `list_orders`, así que
no hay schemas de entrada. Contrato: ai_context/REPORTES_API_CONTRACT.md.
"""

from datetime import datetime

from django.utils import timezone
from ninja import Schema

from orders.models import MissingItemResolution
from orders.report_services import STALL_THRESHOLDS_H


# --- Dashboard 1: Torre de Control Operacional -----------------------------

class StatusCount(Schema):
    status: str
    count: int


class AgingBucket(Schema):
    label: str          # "0-1d", "1-2d", ... "7d+"
    count: int


class OperationsSummaryOut(Schema):
    generated_at: datetime
    period_days: int                      # largo de la ventana de flujo usada

    # Flujo del período (por received_at / completed_at)
    received: int                         # guías ingresadas en la ventana
    produced: int                         # guías producidas (completadas) en la ventana
    received_delta_pct: float | None      # Δ% vs período anterior comparable
    produced_delta_pct: float | None
    incomplete: int                       # incidencias surgidas en la ventana
    incomplete_rate: float                # incomplete / received

    # Turnaround recepción -> producción (guías producidas en la ventana)
    tat_p50_days: float
    tat_p90_days: float
    tat_target_days: int
    tat_on_target_pct: float              # % producidas dentro de la meta

    # Foto de planta AHORA (snapshot, sin ventana de fecha)
    in_plant: int                         # WIP activo (RECIBIDA + EN_REVISION + INCOMPLETA)
    in_plant_by_status: list[StatusCount]
    open_incomplete: int                  # guías en estado INCOMPLETA ahora
    stalled_count: int                    # WIP sobre su umbral de tiempo en estado
    oldest_in_plant_days: float           # antigüedad de la guía más vieja en planta
    aging: list[AgingBucket]              # WIP repartido por tramos de antigüedad
    by_status: list[StatusCount]          # distribución completa por estado (contexto)


class StalledOrderOut(Schema):
    """Una guía que lleva demasiado tiempo en su estado actual.

    `since` viene anotado por el servicio (`state_since`: timestamp de entrada al
    estado con fallback a `received_at`). `age_hours` se deriva de él y
    `threshold_hours` sale del umbral del estado. El servicio ya hizo
    `select_related('company', 'worker')`.
    """

    id: int
    order_number: str | None
    reference: str
    company_name: str
    worker_name: str
    status: str
    since: datetime | None
    age_hours: float
    threshold_hours: int

    @staticmethod
    def resolve_company_name(obj) -> str:
        return obj.company.name

    @staticmethod
    def resolve_worker_name(obj) -> str:
        return obj.worker.full_name

    @staticmethod
    def resolve_since(obj) -> datetime | None:
        return getattr(obj, 'state_since', None)

    @staticmethod
    def resolve_age_hours(obj) -> float:
        since = getattr(obj, 'state_since', None)
        if since is None:
            return 0.0
        return round((timezone.now() - since).total_seconds() / 3600, 1)

    @staticmethod
    def resolve_threshold_hours(obj) -> int:
        return STALL_THRESHOLDS_H[obj.status]


class TimeseriesPoint(Schema):
    date: str          # YYYY-MM-DD (inicio del bucket)
    received: int      # ingresadas
    produced: int      # producidas (completadas): tasa real de salida de planta
    delivered: int     # entregadas (hoy casi siempre 0: no se registra aún)


class TimeseriesOut(Schema):
    granularity: str
    points: list[TimeseriesPoint]


# --- Dashboard 3: Calidad e Incidencias ------------------------------------

class ResolutionMix(Schema):
    encontrada: int
    comprada: int


class QualitySummaryOut(Schema):
    generated_at: datetime
    total_orders: int
    incomplete_orders: int
    incomplete_rate: float
    open_incomplete: int
    discrepancy_rate: float
    resolution_mix: ResolutionMix
    purchase_cost_total: float
    avg_resolution_hours: float


class GarmentParetoRow(Schema):
    name: str
    incident_count: int
    cumulative_pct: float


class GarmentParetoOut(Schema):
    total_incidents: int
    rows: list[GarmentParetoRow]


class IncidentResolutionOut(Schema):
    """Réplica local del resumen de una resolución de prenda faltante.

    No se reutiliza `orders.schemas.MissingItemResolutionOut` para no acoplar la
    reportería a cambios de ese schema; expone solo lo que la tabla de
    incidencias necesita.
    """

    item_name: str
    resolution_type: str
    quantity: int
    purchase_cost: float | None
    resolved_at: datetime

    @staticmethod
    def resolve_item_name(obj) -> str:
        return obj.item.display_name


class IncidentOut(Schema):
    order_id: int
    order_number: str | None
    reference: str
    company_name: str
    worker_name: str
    status: str
    incomplete_at: datetime | None
    completed_at: datetime | None
    age_hours: float | None
    resolutions: list[IncidentResolutionOut]
    observations: str

    @staticmethod
    def resolve_order_id(obj) -> int:
        return obj.id

    @staticmethod
    def resolve_company_name(obj) -> str:
        return obj.company.name

    @staticmethod
    def resolve_worker_name(obj) -> str:
        return obj.worker.full_name

    @staticmethod
    def resolve_resolutions(obj) -> list:
        return list(obj.missing_item_resolutions.all())

    @staticmethod
    def resolve_age_hours(obj) -> float | None:
        # Solo tiene sentido mientras la incidencia sigue abierta (INCOMPLETA).
        if obj.status != 'INCOMPLETA' or obj.incomplete_at is None:
            return None
        return round((timezone.now() - obj.incomplete_at).total_seconds() / 3600, 1)
