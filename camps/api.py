from typing import List

from ninja import Router
from ninja.pagination import paginate

from authentication.auth import JWTAuth
from authentication.permissions import require_admin
from camps import services
from camps.schemas import CampIn, CampOut, FaenaIn, FaenaOut, RoomIn, RoomOut

faenas_router = Router(auth=JWTAuth())
camps_router = Router(auth=JWTAuth())
rooms_router = Router(auth=JWTAuth())


# --- Faenas ---


@faenas_router.get('/', response=List[FaenaOut])
@paginate
def list_faenas(request, search: str | None = None, is_active: bool | None = None):
    return services.list_faenas(search=search, is_active=is_active)


@faenas_router.get('/{faena_id}', response=FaenaOut)
def get_faena(request, faena_id: int):
    return services.get_faena(faena_id)


@faenas_router.post('/', response={201: FaenaOut})
@require_admin()
def create_faena(request, payload: FaenaIn):
    """Crear una faena es excepcional: solo al empezar a atender un sitio nuevo."""
    return 201, services.create_faena(payload)


@faenas_router.put('/{faena_id}', response=FaenaOut)
@require_admin()
def update_faena(request, faena_id: int, payload: FaenaIn):
    return services.update_faena(faena_id, payload)


# --- Campamentos ---


@camps_router.get('/', response=List[CampOut])
@paginate
def list_camps(
    request,
    faena_id: int | None = None,
    search: str | None = None,
    is_active: bool | None = None,
):
    return services.list_camps(faena_id=faena_id, search=search, is_active=is_active)


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
    faena_id: int | None = None,
    search: str | None = None,
    is_active: bool | None = None,
):
    return services.list_rooms(
        camp_id=camp_id, faena_id=faena_id, search=search, is_active=is_active
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
