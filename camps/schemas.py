from uuid import UUID

from ninja import Schema


class CampIn(Schema):
    client_id: int
    name: str
    is_active: bool = True


class CampOut(Schema):
    id: int
    client_id: int
    client_name: str
    name: str
    is_active: bool
    rooms_count: int

    @staticmethod
    def resolve_client_name(obj) -> str:
        return obj.client.name

    @staticmethod
    def resolve_rooms_count(obj) -> int:
        # Viene anotado desde `services.list_camps` para no disparar una
        # consulta por fila (N+1).
        return getattr(obj, 'rooms_count', 0)


class RoomIn(Schema):
    camp_id: int
    number: str
    is_active: bool = True


class RoomOut(Schema):
    id: int
    camp_id: int
    camp_name: str
    client_id: int
    number: str
    qr_code: UUID
    is_active: bool

    @staticmethod
    def resolve_camp_name(obj) -> str:
        return obj.camp.name

    @staticmethod
    def resolve_client_id(obj) -> int:
        return obj.camp.client_id
