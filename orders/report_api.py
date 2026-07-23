"""Router de la reportería en tiempo real (Dashboards 1 y 3).

Solo enruta: recibe los query params, los agrupa y delega en
`report_services`. Toda la lógica vive en el service. Se monta en `core/api.py`
bajo `/api/reports/`. Contrato: ai_context/REPORTES_API_CONTRACT.md.
"""

from datetime import date
from typing import List

from ninja import Router
from ninja.pagination import paginate

from authentication.auth import JWTAuth
from authentication.models import User
from authentication.permissions import require_roles
from orders import report_services as svc
from orders.report_schemas import (
    GarmentParetoOut,
    IncidentOut,
    OperationsSummaryOut,
    QualitySummaryOut,
    StalledOrderOut,
    TimeseriesOut,
)

router = Router(auth=JWTAuth())


def _filters(
    company_id: int | None,
    worker_id: int | None,
    date_from: date | None,
    date_to: date | None,
    delivery_flow: str | None,
    client_id: int | None = None,
) -> dict:
    return {
        'company_id': company_id,
        'client_id': client_id,
        'worker_id': worker_id,
        'date_from': date_from,
        'date_to': date_to,
        'delivery_flow': delivery_flow,
    }


# --- Dashboard 1: Torre de Control Operacional -----------------------------

@router.get('/operations/summary', response=OperationsSummaryOut)
@require_roles(User.Role.SUPERVISOR)
def operations_summary(
    request,
    company_id: int | None = None,
    client_id: int | None = None,
    worker_id: int | None = None,
    date_from: date | None = None,
    date_to: date | None = None,
    delivery_flow: str | None = None,
):
    return svc.get_operations_summary(**_filters(company_id, worker_id, date_from, date_to, delivery_flow, client_id=client_id))


@router.get('/operations/stalled', response=List[StalledOrderOut])
@require_roles(User.Role.SUPERVISOR)
@paginate
def operations_stalled(
    request,
    company_id: int | None = None,
    client_id: int | None = None,
    worker_id: int | None = None,
    date_from: date | None = None,
    date_to: date | None = None,
    delivery_flow: str | None = None,
):
    return svc.get_stalled_orders(**_filters(company_id, worker_id, date_from, date_to, delivery_flow, client_id=client_id))


@router.get('/operations/timeseries', response=TimeseriesOut)
@require_roles(User.Role.SUPERVISOR)
def operations_timeseries(
    request,
    granularity: str = 'day',
    company_id: int | None = None,
    client_id: int | None = None,
    worker_id: int | None = None,
    date_from: date | None = None,
    date_to: date | None = None,
    delivery_flow: str | None = None,
):
    return svc.get_timeseries(granularity, **_filters(company_id, worker_id, date_from, date_to, delivery_flow, client_id=client_id))


# --- Dashboard 3: Calidad e Incidencias ------------------------------------

@router.get('/quality/summary', response=QualitySummaryOut)
@require_roles(User.Role.SUPERVISOR)
def quality_summary(
    request,
    company_id: int | None = None,
    client_id: int | None = None,
    worker_id: int | None = None,
    date_from: date | None = None,
    date_to: date | None = None,
    delivery_flow: str | None = None,
):
    return svc.get_quality_summary(**_filters(company_id, worker_id, date_from, date_to, delivery_flow, client_id=client_id))


@router.get('/quality/garment-pareto', response=GarmentParetoOut)
@require_roles(User.Role.SUPERVISOR)
def quality_pareto(
    request,
    company_id: int | None = None,
    client_id: int | None = None,
    worker_id: int | None = None,
    date_from: date | None = None,
    date_to: date | None = None,
    delivery_flow: str | None = None,
):
    return svc.get_garment_pareto(**_filters(company_id, worker_id, date_from, date_to, delivery_flow, client_id=client_id))


@router.get('/quality/incidents', response=List[IncidentOut])
@require_roles(User.Role.SUPERVISOR)
@paginate
def quality_incidents(
    request,
    resolution_type: str | None = None,
    company_id: int | None = None,
    client_id: int | None = None,
    worker_id: int | None = None,
    date_from: date | None = None,
    date_to: date | None = None,
    delivery_flow: str | None = None,
):
    return svc.get_incidents(
        resolution_type, **_filters(company_id, worker_id, date_from, date_to, delivery_flow, client_id=client_id)
    )
