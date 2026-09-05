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
    """Sincroniza una entrega móvil con ubicación obligatoria.

    Flujo 1 exige el QR de la puerta. Un 409 indica que no coincide con el
    destino y requiere una segunda confirmación. Flujo 2 omite la puerta y
    registra que el morral fue entregado al cliente.
    """
    try:
        result = services.confirm_delivery_by_scan(
            client_uuid=payload.client_uuid,
            order_code=payload.order_code,
            room_qr=payload.room_qr,
            user=request.auth,
            latitude=payload.latitude,
            longitude=payload.longitude,
            accuracy_meters=payload.accuracy_meters,
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
