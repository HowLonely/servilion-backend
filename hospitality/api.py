from datetime import datetime
from typing import List

from ninja import Router
from ninja.pagination import paginate

from authentication.auth import JWTAuth
from authentication.models import User
from authentication.permissions import require_roles
from common.schemas import MessageOut
from hospitality import services
from hospitality.schemas import (
    BatchNoteOut,
    DispatchIn,
    HospitalityCountersOut,
    LinenBatchIn,
    LinenBatchOut,
    ReturnCountBatchIn,
)

router = Router(auth=JWTAuth())

# Igual que en `orders.api`: las rutas literales van declaradas antes que las de
# parámetro, porque Django Ninja resuelve por forma de URL antes que por método.


@router.get('/', response=List[LinenBatchOut])
@paginate
def list_batches(
    request,
    status: str | None = None,
    company_id: int | None = None,
    search: str | None = None,
    date_from: datetime | None = None,
    date_to: datetime | None = None,
):
    return services.list_batches(
        status=status,
        company_id=company_id,
        search=search,
        date_from=date_from,
        date_to=date_to,
    )


@router.post('/', response={201: LinenBatchOut, 400: MessageOut})
@require_roles(User.Role.DIGITADOR_OT, User.Role.SUPERVISOR)
def create_batch(request, payload: LinenBatchIn):
    """Registra la llegada de una carga de lencería sucia del campamento."""
    try:
        batch = services.create_batch(payload, received_by=request.auth)
    except services.BatchFlowError as exc:
        return 400, {'detail': str(exc)}
    return 201, services.get_batch(batch.id)


@router.get('/counters', response=HospitalityCountersOut)
def get_counters(request, date_from: datetime | None = None, date_to: datetime | None = None):
    """Indicadores del servicio: lotes en planta, piezas y merma acumulada."""
    return services.get_hospitality_counters(date_from=date_from, date_to=date_to)


@router.get('/{batch_id}', response=LinenBatchOut)
def get_batch(request, batch_id: int):
    return services.get_batch(batch_id)


@router.get('/{batch_id}/note', response=BatchNoteOut)
def get_batch_note(request, batch_id: int):
    """Acta de devolución que el encargado del campamento revisa y firma."""
    return services.build_batch_note(batch_id)


@router.post('/{batch_id}/process', response={200: LinenBatchOut, 400: MessageOut})
@require_roles(User.Role.DIGITADOR_EMPAQUE, User.Role.SUPERVISOR)
def start_processing(request, batch_id: int):
    try:
        return 200, services.start_processing(batch_id, user=request.auth)
    except services.BatchFlowError as exc:
        return 400, {'detail': str(exc)}


@router.post('/{batch_id}/return-count', response={200: LinenBatchOut, 400: MessageOut})
@require_roles(User.Role.DIGITADOR_EMPAQUE, User.Role.SUPERVISOR)
def register_return_count(request, batch_id: int, payload: ReturnCountBatchIn):
    """Cuenta de salida por tipo de lencería: es donde aparece la merma."""
    try:
        return 200, services.register_return_count(batch_id, payload.counts, user=request.auth)
    except services.BatchFlowError as exc:
        return 400, {'detail': str(exc)}


@router.post('/{batch_id}/dispatch', response={200: LinenBatchOut, 400: MessageOut})
@require_roles(User.Role.SUPERVISOR)
def dispatch_batch(request, batch_id: int, payload: DispatchIn):
    """Despacha la carga limpia de vuelta a faena y cierra el lote."""
    try:
        return 200, services.dispatch_batch(
            batch_id, user=request.auth, received_by_client=payload.received_by_client, note=payload.note
        )
    except services.BatchFlowError as exc:
        return 400, {'detail': str(exc)}
