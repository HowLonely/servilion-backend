from ninja import Schema
from pydantic import Field


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
    role_name: str
    # Permisos efectivos del rol. Los clientes (web, terminal, app) deciden qué
    # pantallas ofrecer con esto, en vez de repetir listas de roles a mano.
    permissions: list[str]
    phone: str
    is_active: bool

    @staticmethod
    def resolve_permissions(obj) -> list[str]:
        from authentication.permissions import user_permissions

        return sorted(user_permissions(obj))


class TokenOut(Schema):
    access: str
    refresh: str
    user: UserOut


# --- Módulo de configuración: usuarios -------------------------------------


class StaffUserOut(UserOut):
    last_login: str | None = None

    @staticmethod
    def resolve_last_login(obj) -> str | None:
        return obj.last_login.isoformat() if obj.last_login else None


class StaffUserCreateIn(Schema):
    username: str = Field(min_length=1, max_length=150)
    first_name: str = ''
    last_name: str = ''
    email: str = ''
    phone: str = ''
    role: str
    password: str = Field(min_length=1)
    is_active: bool = True


class StaffUserUpdateIn(Schema):
    username: str = Field(min_length=1, max_length=150)
    first_name: str = ''
    last_name: str = ''
    email: str = ''
    phone: str = ''
    role: str
    is_active: bool = True


class PasswordSetIn(Schema):
    password: str = Field(min_length=1)


# --- Módulo de configuración: roles ----------------------------------------


class PermissionOut(Schema):
    code: str
    label: str
    group: str
    description: str


class RoleOut(Schema):
    id: int
    code: str
    name: str
    description: str
    permissions: list[str]
    is_system: bool
    is_active: bool
    # ADMIN: tiene siempre todos los permisos y no se edita.
    is_superrole: bool
    user_count: int = 0

    @staticmethod
    def resolve_permissions(obj) -> list[str]:
        from authentication.permissions import role_permissions

        return sorted(role_permissions(obj.code)) if obj.is_active else sorted(obj.permissions)

    @staticmethod
    def resolve_is_superrole(obj) -> bool:
        from authentication.models import User

        return obj.code == User.Role.ADMIN


class RoleIn(Schema):
    name: str = Field(min_length=1, max_length=60)
    description: str = ''
    permissions: list[str] = []
    is_active: bool = True
