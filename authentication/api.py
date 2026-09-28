from typing import List

from ninja import Router
from ninja.pagination import paginate

from authentication.auth import JWTAuth
from authentication.permissions import PERMISSIONS, Perm, require_permission
from authentication.schemas import (
    LoginIn,
    PasswordSetIn,
    PermissionOut,
    RefreshIn,
    RoleIn,
    RoleOut,
    StaffUserCreateIn,
    StaffUserOut,
    StaffUserUpdateIn,
    TokenOut,
    UserOut,
)
from authentication.services import (
    AuthError,
    UserAdminError,
    authenticate_user,
    create_role,
    create_staff_user,
    delete_role,
    get_role,
    get_staff_user,
    issue_tokens,
    list_roles,
    list_staff_users,
    refresh_access_token,
    set_staff_password,
    update_role,
    update_staff_user,
)
from common.schemas import MessageOut

router = Router()
users_router = Router(auth=JWTAuth())
roles_router = Router(auth=JWTAuth())


@router.post('/login', response={200: TokenOut, 401: MessageOut}, auth=None)
def login(request, payload: LoginIn):
    try:
        user = authenticate_user(payload.username, payload.password)
    except AuthError as exc:
        return 401, {'detail': str(exc)}
    return 200, issue_tokens(user)


@router.post('/refresh', response={200: TokenOut, 401: MessageOut}, auth=None)
def refresh(request, payload: RefreshIn):
    try:
        return 200, refresh_access_token(payload.refresh)
    except AuthError as exc:
        return 401, {'detail': str(exc)}


@router.get('/me', response=UserOut, auth=JWTAuth())
def me(request):
    return request.auth


# --- Usuarios -----------------------------------------------------------------


@users_router.get('/', response=List[StaffUserOut])
@require_permission(Perm.USERS)
@paginate
def list_users(request, search: str | None = None, role: str | None = None, is_active: bool | None = None):
    return list_staff_users(search=search, role=role, is_active=is_active)


@users_router.get('/{user_id}', response=StaffUserOut)
@require_permission(Perm.USERS)
def get_user(request, user_id: int):
    return get_staff_user(user_id)


@users_router.post('/', response={201: StaffUserOut, 400: MessageOut})
@require_permission(Perm.USERS)
def create_user(request, payload: StaffUserCreateIn):
    try:
        return 201, create_staff_user(payload, actor=request.auth)
    except UserAdminError as exc:
        return 400, {'detail': str(exc)}


@users_router.put('/{user_id}', response={200: StaffUserOut, 400: MessageOut})
@require_permission(Perm.USERS)
def update_user(request, user_id: int, payload: StaffUserUpdateIn):
    try:
        return 200, update_staff_user(user_id, payload, actor=request.auth)
    except UserAdminError as exc:
        return 400, {'detail': str(exc)}


@users_router.post('/{user_id}/password', response={200: StaffUserOut, 400: MessageOut})
@require_permission(Perm.USERS)
def set_password(request, user_id: int, payload: PasswordSetIn):
    try:
        return 200, set_staff_password(user_id, payload.password, actor=request.auth)
    except UserAdminError as exc:
        return 400, {'detail': str(exc)}


# --- Roles --------------------------------------------------------------------


# Ruta literal antes de '/{role_id}' (Ninja resuelve por forma de URL).
@roles_router.get('/permissions', response=List[PermissionOut])
def list_permissions(request):
    """Catálogo de permisos asignables, agrupado para la pantalla de roles."""
    return list(PERMISSIONS)


@roles_router.get('/', response=List[RoleOut])
def list_all_roles(request, include_inactive: bool = True):
    # Sin permiso especial: el selector de rol del formulario de usuario y la
    # cabecera de cada cliente necesitan leer los nombres.
    return list(list_roles(include_inactive=include_inactive))


@roles_router.get('/{role_id}', response=RoleOut)
def get_one_role(request, role_id: int):
    return get_role(role_id)


@roles_router.post('/', response={201: RoleOut, 400: MessageOut})
@require_permission(Perm.USERS)
def create_one_role(request, payload: RoleIn):
    try:
        return 201, create_role(payload, actor=request.auth)
    except UserAdminError as exc:
        return 400, {'detail': str(exc)}


@roles_router.put('/{role_id}', response={200: RoleOut, 400: MessageOut})
@require_permission(Perm.USERS)
def update_one_role(request, role_id: int, payload: RoleIn):
    try:
        return 200, update_role(role_id, payload, actor=request.auth)
    except UserAdminError as exc:
        return 400, {'detail': str(exc)}


@roles_router.delete('/{role_id}', response={204: None, 400: MessageOut})
@require_permission(Perm.USERS)
def delete_one_role(request, role_id: int):
    try:
        delete_role(role_id, actor=request.auth)
    except UserAdminError as exc:
        return 400, {'detail': str(exc)}
    return 204, None
