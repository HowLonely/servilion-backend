from ninja import Schema


class WorkerIn(Schema):
    company_id: int
    badge_code: str
    full_name: str
    national_id: str = ''
    camp: str = ''
    room: str = ''
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
    camp: str
    room: str
    shift: str
    position: str
    area: str
    phone: str
    is_active: bool
