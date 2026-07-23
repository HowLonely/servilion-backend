from ninja import Schema


class GarmentTypeIn(Schema):
    code: str
    name: str
    is_active: bool = True


class GarmentTypeOut(Schema):
    id: int
    code: str
    name: str
    is_active: bool
