from datetime import datetime
from typing import List

from ninja import Router
from ninja.pagination import paginate

from authentication.auth import JWTAuth
from authentication.permissions import Perm, require_permission
from common.node import plant_only
from common.schemas import MessageOut
from hospitality import services
from hospitality.schemas import (
    CompanyBalanceOut,
    CountIn,
    DispatchIn,
    DispatchPrintJobOut,
    FieldMovementBatchIn,
    FieldMovementBatchOut,
    LinenMovementOut,
    VoidMovementIn,
)

router = Router(auth=JWTAuth())

# Igual que en `orders.api`: las rutas literales van declaradas antes que las de
# parámetro, porque Django Ninja resuelve por forma de URL antes que por método.


@router.get('/balances', response=List[CompanyBalanceOut])
def get_balances(request, company_id: int | None = None):
    """Dónde está la lencería de cada cliente: Servilion, bodega de faena y campamentos.

    Sin `company_id` trae todos los clientes de hotelería: es lo que baja la app
    móvil para ver el saldo del campamento sin señal.
    """
    return services.compute_all_balances(company_id=company_id)


@router.get('/movements', response=List[LinenMovementOut])
@paginate
def list_movements(
    request,
    company_id: int | None = None,
    camp_id: int | None = None,
    kind: str | None = None,
    include_voided: bool = True,
    date_from: datetime | None = None,
    date_to: datetime | None = None,
):
    return services.list_movements(
        company_id=company_id,
        camp_id=camp_id,
        kind=kind,
        include_voided=include_voided,
        date_from=date_from,
        date_to=date_to,
    )


@router.post('/dispatches', response={201: LinenMovementOut, 400: MessageOut})
@require_permission(Perm.LINEN_DISPATCH)
@plant_only
def register_dispatch(request, payload: DispatchIn):
    """Despacho de lencería limpia desde la planta a la faena del cliente."""
    try:
        movement = services.register_dispatch(
            payload.company_id, payload.lines, user=request.auth, note=payload.note
        )
    except services.LinenFlowError as error:
        return 400, {'detail': str(error)}
    return 201, movement


@router.post('/counts', response={201: LinenMovementOut, 400: MessageOut})
@require_permission(Perm.LINEN_MANAGE)
def register_count(request, payload: CountIn):
    """Conteo de inventario de un campamento o de la bodega de faena."""
    try:
        movement = services.register_count(
            payload.company_id, payload.camp_id, payload.lines, user=request.auth, note=payload.note
        )
    except services.LinenFlowError as error:
        return 400, {'detail': str(error)}
    return 201, movement


@router.post('/field-sync', response=FieldMovementBatchOut)
@require_permission(Perm.FIELD_LINEN)
def sync_field_movements(request, payload: FieldMovementBatchIn):
    """Cola de repartos y retiros que la app móvil registró en faena.

    Cada movimiento se resuelve por separado: uno rechazado no bloquea al resto
    de la cola, y reenviar uno ya recibido responde DUPLICADO sin repetirlo.
    """
    return {
        'results': [services.sync_field_movement(movement, user=request.auth) for movement in payload.movements]
    }


@router.get('/movements/{movement_id}', response=LinenMovementOut)
def get_movement(request, movement_id: int):
    return services.get_movement(movement_id)


@router.get('/movements/{movement_id}/print', response={200: DispatchPrintJobOut, 400: MessageOut})
def get_dispatch_print_job(request, movement_id: int):
    """Datos de la guía de despacho para la etiquetera de la planta."""
    try:
        return 200, services.build_dispatch_print_job(movement_id)
    except services.LinenFlowError as error:
        return 400, {'detail': str(error)}


@router.post('/movements/{movement_id}/void', response={200: LinenMovementOut, 400: MessageOut})
@require_permission(Perm.LINEN_MANAGE)
def void_movement(request, movement_id: int, payload: VoidMovementIn):
    """Anula un movimiento mal registrado; su efecto sale de los saldos."""
    try:
        return 200, services.void_movement(movement_id, user=request.auth, reason=payload.reason)
    except services.LinenFlowError as error:
        return 400, {'detail': str(error)}
