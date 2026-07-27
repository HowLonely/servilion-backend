from uuid import UUID

from django.db.models import Count, Q, QuerySet

from camps.models import Camp, Room
from camps.schemas import CampIn, RoomIn


# --- Campamentos ---


def list_camps(
    client_id: int | None = None,
    search: str | None = None,
    is_active: bool | None = None,
) -> QuerySet[Camp]:
    queryset = Camp.objects.select_related('client').annotate(
        rooms_count=Count('rooms', filter=Q(rooms__is_active=True))
    )
    if client_id is not None:
        queryset = queryset.filter(client_id=client_id)
    if search:
        queryset = queryset.filter(name__icontains=search)
    if is_active is not None:
        queryset = queryset.filter(is_active=is_active)
    # Orden determinístico: LIMIT/OFFSET sin order_by no garantiza páginas
    # estables entre requests.
    return queryset.order_by('client__name', 'name')


def get_camp(camp_id: int) -> Camp:
    return Camp.objects.select_related('client').get(pk=camp_id)


def create_camp(payload: CampIn) -> Camp:
    camp = Camp.objects.create(**payload.dict())
    return get_camp(camp.id)


def update_camp(camp_id: int, payload: CampIn) -> Camp:
    camp = get_camp(camp_id)
    for field, value in payload.dict().items():
        setattr(camp, field, value)
    camp.save()
    return camp


def deactivate_camp(camp_id: int) -> Camp:
    camp = get_camp(camp_id)
    camp.is_active = False
    camp.save(update_fields=['is_active', 'updated_at'])
    return camp


# --- Habitaciones ---


def list_rooms(
    camp_id: int | None = None,
    client_id: int | None = None,
    search: str | None = None,
    is_active: bool | None = None,
) -> QuerySet[Room]:
    queryset = Room.objects.select_related('camp', 'camp__client')
    if camp_id is not None:
        queryset = queryset.filter(camp_id=camp_id)
    if client_id is not None:
        queryset = queryset.filter(camp__client_id=client_id)
    if search:
        queryset = queryset.filter(number__icontains=search)
    if is_active is not None:
        queryset = queryset.filter(is_active=is_active)
    return queryset.order_by('camp__name', 'number')


def get_room(room_id: int) -> Room:
    return Room.objects.select_related('camp', 'camp__client').get(pk=room_id)


def get_room_by_qr(qr_code: UUID) -> Room:
    """Resuelve el QR pegado en la puerta a su habitación.

    Lo usa la app móvil al entregar el morral (ver `orders.delivery_api`).
    """
    return Room.objects.select_related('camp', 'camp__client').get(qr_code=qr_code)


def create_room(payload: RoomIn) -> Room:
    room = Room.objects.create(**payload.dict())
    return get_room(room.id)


def update_room(room_id: int, payload: RoomIn) -> Room:
    """El `qr_code` no se toca: ya está impreso y pegado en la puerta."""
    room = get_room(room_id)
    for field, value in payload.dict().items():
        setattr(room, field, value)
    room.save()
    return room


def deactivate_room(room_id: int) -> Room:
    room = get_room(room_id)
    room.is_active = False
    room.save(update_fields=['is_active', 'updated_at'])
    return room
