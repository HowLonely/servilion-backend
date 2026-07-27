from ninja import Schema


class WorkerIn(Schema):
    company_id: int
    badge_code: str
    full_name: str
    national_id: str = ''
    # Reemplaza a los antiguos `camp`/`room` de texto libre. Null mientras no se
    # le asigne pieza; el campamento se deduce de la habitación.
    current_room_id: int | None = None
    shift: str = ''
    position: str = ''
    area: str = ''
    phone: str = ''


class WorkerOut(Schema):
    id: int
    company_id: int
    badge_code: str
    full_name: str
    national_id: str
    current_room_id: int | None
    room_number: str
    camp_id: int | None
    camp_name: str
    shift: str
    position: str
    area: str
    phone: str
    is_active: bool

    @staticmethod
    def resolve_room_number(obj) -> str:
        return obj.current_room.number if obj.current_room_id else ''

    @staticmethod
    def resolve_camp_id(obj) -> int | None:
        return obj.current_room.camp_id if obj.current_room_id else None

    @staticmethod
    def resolve_camp_name(obj) -> str:
        return obj.current_room.camp.name if obj.current_room_id else ''
