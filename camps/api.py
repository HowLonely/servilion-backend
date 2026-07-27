from typing import List

from ninja import Router
from ninja.pagination import paginate

from authentication.auth import JWTAuth
from authentication.permissions import require_admin
from camps import services
from camps.schemas import CampIn, CampOut, RoomIn, RoomOut

camps_router = Router(auth=JWTAuth())
rooms_router = Router(auth=JWTAuth())


# --- Campamentos ---


@camps_router.get('/', response=List[CampOut])
@paginate
def list_camps(
    request,
    client_id: int | None = None,
    search: str | None = None,
    is_active: bool | None = None,
):
    return services.list_camps(client_id=client_id, search=search, is_active=is_active)


@camps_router.get('/{camp_id}', response=CampOut)
def get_camp(request, camp_id: int):
    return services.get_camp(camp_id)


@camps_router.post('/', response={201: CampOut})
@require_admin()
def create_camp(request, payload: CampIn):
    return 201, services.create_camp(payload)


@camps_router.put('/{camp_id}', response=CampOut)
@require_admin()
def update_camp(request, camp_id: int, payload: CampIn):
    return services.update_camp(camp_id, payload)


@camps_router.delete('/{camp_id}', response={204: None})
@require_admin()
def deactivate_camp(request, camp_id: int):
    services.deactivate_camp(camp_id)
    return 204, None


# --- Habitaciones ---


@rooms_router.get('/', response=List[RoomOut])
@paginate
def list_rooms(
    request,
    camp_id: int | None = None,
    client_id: int | None = None,
    search: str | None = None,
    is_active: bool | None = None,
):
    return services.list_rooms(
        camp_id=camp_id, client_id=client_id, search=search, is_active=is_active
    )


@rooms_router.get('/{room_id}', response=RoomOut)
def get_room(request, room_id: int):
    return services.get_room(room_id)


@rooms_router.post('/', response={201: RoomOut})
@require_admin()
def create_room(request, payload: RoomIn):
    """El `qr_code` lo genera el servidor; se imprime y se pega en la puerta."""
    return 201, services.create_room(payload)


@rooms_router.put('/{room_id}', response=RoomOut)
@require_admin()
def update_room(request, room_id: int, payload: RoomIn):
    return services.update_room(room_id, payload)


@rooms_router.delete('/{room_id}', response={204: None})
@require_admin()
def deactivate_room(request, room_id: int):
    services.deactivate_room(room_id)
    return 204, None
