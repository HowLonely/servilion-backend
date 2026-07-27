from ninja import Router

from authentication.auth import JWTAuth
from authentication.models import User
from authentication.permissions import require_roles
from common.schemas import MessageOut
from camps.models import Room
from orders import services
from orders.models import LaundryOrder
from orders.schemas import DeliveryConfirmIn, DeliveryConfirmOut, DeliveryMismatchOut

router = Router(auth=JWTAuth())


@router.post(
    '/confirm',
    response={
        200: DeliveryConfirmOut,
        400: MessageOut,
        404: MessageOut,
        409: DeliveryMismatchOut,
    },
)
@require_roles(User.Role.SUPERVISOR)
def confirm_delivery(request, payload: DeliveryConfirmIn):
    """Registra la entrega del morral tras escanear la OT y el QR de la puerta.

    409 significa que la puerta escaneada no es el destino de la guía: la app
    debe mostrar ambas habitaciones y reenviar con `confirm_different_room` si
    el operador confirma que el trabajador se mudó.
    """
    try:
        result = services.confirm_delivery_by_scan(
            order_code=payload.order_code,
            room_qr=payload.room_qr,
            user=request.auth,
            note=payload.note,
            delivered_at=payload.delivered_at,
            confirm_different_room=payload.confirm_different_room,
        )
    except LaundryOrder.DoesNotExist:
        return 404, {'detail': f'No existe una guía con el código "{payload.order_code}".'}
    except Room.DoesNotExist:
        return 404, {'detail': 'El código QR escaneado no corresponde a ninguna habitación registrada.'}
    except services.DeliveryRoomMismatch as exc:
        return 409, {
            'detail': str(exc),
            'scanned_room': exc.scanned,
            'expected_room': exc.expected,
        }
    except (services.OrderFlowError, services.InvalidStatusTransition) as exc:
        return 400, {'detail': str(exc)}

    return 200, result
