from ninja import Schema


class LoginIn(Schema):
    username: str
    password: str


class RefreshIn(Schema):
    refresh: str


class UserOut(Schema):
    id: int
    username: str
    first_name: str
    last_name: str
    email: str
    role: str
    phone: str
    is_active: bool


class TokenOut(Schema):
    access: str
    refresh: str
    user: UserOut
