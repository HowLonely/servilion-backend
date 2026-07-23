"""Schemas de salida de la reportería en tiempo real (Dashboards 1 y 3).

Solo salida (`Out`): estos endpoints son de lectura agregada. Los filtros viajan
como query params planos en `report_api.py`, igual que en `list_orders`, así que
no hay schemas de entrada. Contrato: ai_context/REPORTES_API_CONTRACT.md.
"""

from datetime import datetime

from django.utils import timezone
from ninja import Schema

from orders.models import MissingItemResolution
from orders.report_services import STALL_THRESHOLDS_H, STALL_TIMESTAMP


# --- Dashboard 1: Torre de Control Operacional -----------------------------

class StatusCount(Schema):
    status: str
    count: int


class OperationsSummaryOut(Schema):
    generated_at: datetime
    wip_total: int
    by_status: list[StatusCount]
    stalled_count: int
    at_risk_count: int
    avg_wip_age_days: float
    received_today: int
    delivered_today: int


class StalledOrderOut(Schema):
    """Una guía que lleva demasiado tiempo en su estado actual.

    `since`, `age_hours` y `threshold_hours` dependen del estado, así que se
    resuelven por fila leyendo el timestamp de entrada que le corresponde
    (STALL_TIMESTAMP). El servicio ya hizo `select_related('company', 'worker')`.
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
    promised_at: datetime | None

    @staticmethod
    def resolve_company_name(obj) -> str:
        return obj.company.name

    @staticmethod
    def resolve_worker_name(obj) -> str:
        return obj.worker.full_name

    @staticmethod
    def resolve_since(obj) -> datetime | None:
        return getattr(obj, STALL_TIMESTAMP[obj.status], None)

    @staticmethod
    def resolve_age_hours(obj) -> float:
        since = getattr(obj, STALL_TIMESTAMP[obj.status], None)
        if since is None:
            return 0.0
        return round((timezone.now() - since).total_seconds() / 3600, 1)

    @staticmethod
    def resolve_threshold_hours(obj) -> int:
        return STALL_THRESHOLDS_H[obj.status]


class TimeseriesPoint(Schema):
    date: str          # YYYY-MM-DD (inicio del bucket)
    received: int
    delivered: int


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
