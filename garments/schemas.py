from ninja import Schema


class GarmentTypeIn(Schema):
    code: str
    name: str
    is_active: bool = True
    is_linen: bool = False


class GarmentTypeOut(Schema):
    id: int
    code: str
    name: str
    is_active: bool
    is_linen: bool
