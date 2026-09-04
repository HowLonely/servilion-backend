from datetime import datetime
from typing import List

from ninja import Router

from authentication.auth import JWTAuth
from authentication.models import User
from authentication.permissions import require_roles
from common.schemas import MessageOut
from weighing import services
from weighing.schemas import PrintJobOut, VoidWeighInIn, WeighInIn, WeighInOut

router = Router(auth=JWTAuth())

# Igual que en `orders.api`: las rutas literales van declaradas ANTES de las que
# llevan parámetros de path, porque Django Ninja resuelve por forma de URL antes
# de mirar el método HTTP y "/pending/{reference}" calzaría con "/{weigh_in_id}".


@router.post('/', response={201: WeighInOut, 400: MessageOut})
@require_roles(User.Role.PESAJE, User.Role.SUPERVISOR)
def create_weigh_in(request, payload: WeighInIn):
    """Pesa el morral, emite el ref y crea sus etiquetas."""
    try:
        weigh_in = services.create_weigh_in(
            client_id=payload.client_id,
            company_id=payload.company_id,
            garment_count=payload.garment_count,
            weight_kg=payload.weight_kg,
            weighed_by=request.auth,
        )
    except services.WeighInError as error:
        return 400, {'detail': str(error)}
    return 201, weigh_in


@router.get('/', response=List[WeighInOut])
def list_weigh_ins(
    request,
    status: str | None = None,
    mine: bool = False,
    since: datetime | None = None,
    limit: int = 50,
):
    """Pesajes recientes. `mine=true` es "los de mi turno" en la báscula."""
    return services.list_weigh_ins(
        status=status,
        weighed_by_id=request.auth.id if mine else None,
        since=since,
        limit=limit,
    )


@router.get('/pending/{reference}', response={200: WeighInOut, 400: MessageOut})
def find_pending_weigh_in(request, reference: str):
    """Resuelve el ref del ticket maestro para que la digitación lo consuma.

    Sin restricción de rol: quien digitaliza necesita leerlo, y no expone nada
    que la guía resultante no vaya a mostrar de todas formas.
    """
    try:
        return 200, services.find_pending_by_reference(reference)
    except services.WeighInError as error:
        return 400, {'detail': str(error)}


@router.get('/{weigh_in_id}', response=WeighInOut)
def get_weigh_in(request, weigh_in_id: int):
    return services.get_weigh_in(weigh_in_id)


@router.get('/{weigh_in_id}/print', response=PrintJobOut)
def get_print_job(request, weigh_in_id: int):
    """Datos para (re)imprimir los adhesivos y el ticket maestro."""
    return services.build_print_job(weigh_in_id)


@router.post('/{weigh_in_id}/void', response={200: WeighInOut, 400: MessageOut})
@require_roles(User.Role.PESAJE, User.Role.SUPERVISOR)
def void_weigh_in(request, weigh_in_id: int, payload: VoidWeighInIn):
    try:
        return 200, services.void_weigh_in(weigh_in_id, request.auth, payload.reason)
    except services.WeighInError as error:
        return 400, {'detail': str(error)}
