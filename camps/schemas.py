from uuid import UUID

from ninja import Schema


class FaenaIn(Schema):
    name: str
    is_active: bool = True


class FaenaOut(Schema):
    id: int
    name: str
    is_active: bool
    camps_count: int

    @staticmethod
    def resolve_camps_count(obj) -> int:
        # Viene anotado desde `services.list_faenas` para no disparar una
        # consulta por fila (N+1).
        return getattr(obj, 'camps_count', 0)


class CampIn(Schema):
    faena_id: int
    name: str
    is_active: bool = True


class CampOut(Schema):
    id: int
    faena_id: int
    faena_name: str
    name: str
    is_active: bool
    rooms_count: int

    @staticmethod
    def resolve_faena_name(obj) -> str:
        return obj.faena.name

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
    faena_id: int
    number: str
    qr_code: UUID
    is_active: bool

    @staticmethod
    def resolve_camp_name(obj) -> str:
        return obj.camp.name

    @staticmethod
    def resolve_faena_id(obj) -> int:
        return obj.camp.faena_id
