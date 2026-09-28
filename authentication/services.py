import re
import unicodedata
from datetime import datetime, timezone

import jwt
from django.conf import settings
from django.contrib.auth import authenticate
from django.contrib.auth.password_validation import validate_password
from django.core.exceptions import ValidationError as DjangoValidationError
from django.db import transaction
from django.db.models import Count, Q, QuerySet

from authentication.models import StaffRole, User
from authentication.permissions import (
    ALL_PERMISSIONS,
    PermissionDenied,
    is_admin,
    role_permissions,
    user_permissions,
)
from authentication.schemas import RoleIn, StaffUserCreateIn, StaffUserUpdateIn


class AuthError(Exception):
    """Credenciales, token o refresh token inválido/expirado."""


class UserAdminError(Exception):
    """Operación inválida del módulo de usuarios y roles (se responde 400)."""


def _build_token(user: User, token_type: str, lifetime) -> str:
    now = datetime.now(timezone.utc)
    payload = {
        'user_id': user.id,
        'type': token_type,
        'iat': now,
        'exp': now + lifetime,
    }
    return jwt.encode(payload, settings.JWT_SECRET, algorithm=settings.JWT_ALGORITHM)


def issue_tokens(user: User) -> dict:
    return {
        'access': _build_token(user, 'access', settings.JWT_ACCESS_TOKEN_LIFETIME),
        'refresh': _build_token(user, 'refresh', settings.JWT_REFRESH_TOKEN_LIFETIME),
        'user': user,
    }


def authenticate_user(username: str, password: str) -> User:
    user = authenticate(username=username, password=password)
    if user is None or not user.is_active or user.is_service_account:
        raise AuthError('Usuario o contraseña incorrectos.')
    return user


def decode_token(token: str, expected_type: str) -> dict:
    try:
        payload = jwt.decode(token, settings.JWT_SECRET, algorithms=[settings.JWT_ALGORITHM])
    except jwt.PyJWTError as exc:
        raise AuthError('Token inválido o expirado.') from exc

    if payload.get('type') != expected_type:
        raise AuthError('Tipo de token inválido.')
    return payload


def get_user_from_token(token: str, expected_type: str = 'access') -> User:
    payload = decode_token(token, expected_type)
    try:
        return User.objects.get(pk=payload['user_id'], is_active=True)
    except User.DoesNotExist as exc:
        raise AuthError('Usuario no encontrado o inactivo.') from exc


def refresh_access_token(refresh_token: str) -> dict:
    user = get_user_from_token(refresh_token, expected_type='refresh')
    return issue_tokens(user)


# --- Módulo de configuración: usuarios ---------------------------------------


def list_staff_users(
    search: str | None = None, role: str | None = None, is_active: bool | None = None
) -> QuerySet[User]:
    queryset = User.objects.filter(is_service_account=False)
    if search:
        queryset = queryset.filter(
            Q(username__icontains=search) | Q(first_name__icontains=search) | Q(last_name__icontains=search)
        )
    if role:
        queryset = queryset.filter(role=role)
    if is_active is not None:
        queryset = queryset.filter(is_active=is_active)
    return queryset.order_by('username')


def get_staff_user(user_id: int) -> User:
    return User.objects.get(pk=user_id, is_service_account=False)


def _check_can_assign_role(actor: User, role_code: str) -> None:
    """Valida el rol a asignar y que quien lo asigna no se esté dando más de lo que tiene.

    Sin esta regla, cualquiera con permiso de usuarios podría crearse una
    cuenta ADMIN y saltarse toda la separación de funciones.
    """
    role = StaffRole.objects.filter(code=role_code).first()
    if role is None:
        raise UserAdminError(f'El rol "{role_code}" no existe.')
    if not role.is_active:
        raise UserAdminError(f'El rol "{role.name}" está desactivado.')
    if is_admin(actor):
        return
    if role_code == User.Role.ADMIN:
        raise PermissionDenied('Solo un administrador puede asignar el rol Administrador.')
    missing = role_permissions(role_code) - user_permissions(actor)
    if missing:
        raise PermissionDenied('No puedes asignar un rol con permisos que tú no tienes.')


def _validate_password(password: str, user: User) -> None:
    try:
        validate_password(password, user)
    except DjangoValidationError as exc:
        raise UserAdminError(' '.join(exc.messages)) from None


def _ensure_username_free(username: str, exclude_id: int | None = None) -> None:
    queryset = User.objects.filter(username__iexact=username)
    if exclude_id is not None:
        queryset = queryset.exclude(pk=exclude_id)
    if queryset.exists():
        raise UserAdminError(f'Ya existe un usuario "{username}".')


def _remaining_active_admins(exclude_id: int) -> int:
    return (
        User.objects.filter(role=User.Role.ADMIN, is_active=True, is_service_account=False)
        .exclude(pk=exclude_id)
        .count()
    )


@transaction.atomic
def create_staff_user(payload: StaffUserCreateIn, actor: User) -> User:
    username = payload.username.strip()
    _ensure_username_free(username)
    _check_can_assign_role(actor, payload.role)
    user = User(
        username=username,
        first_name=payload.first_name.strip(),
        last_name=payload.last_name.strip(),
        email=payload.email.strip(),
        phone=payload.phone.strip(),
        role=payload.role,
        is_active=payload.is_active,
    )
    _validate_password(payload.password, user)
    user.set_password(payload.password)
    user.save()
    return user


@transaction.atomic
def update_staff_user(user_id: int, payload: StaffUserUpdateIn, actor: User) -> User:
    user = User.objects.select_for_update().get(pk=user_id, is_service_account=False)
    username = payload.username.strip()
    _ensure_username_free(username, exclude_id=user.id)

    if payload.role != user.role:
        _check_can_assign_role(actor, payload.role)
        if user.role == User.Role.ADMIN and not is_admin(actor):
            raise PermissionDenied('Solo un administrador puede cambiarle el rol a otro administrador.')

    losing_admin = user.role == User.Role.ADMIN and (payload.role != User.Role.ADMIN or not payload.is_active)
    if losing_admin and _remaining_active_admins(user.id) == 0:
        raise UserAdminError('Tiene que quedar al menos un administrador activo.')
    if user.id == actor.id and not payload.is_active:
        raise UserAdminError('No puedes desactivar tu propia cuenta.')

    user.username = username
    user.first_name = payload.first_name.strip()
    user.last_name = payload.last_name.strip()
    user.email = payload.email.strip()
    user.phone = payload.phone.strip()
    user.role = payload.role
    user.is_active = payload.is_active
    user.save()
    return user


@transaction.atomic
def set_staff_password(user_id: int, password: str, actor: User) -> User:
    user = User.objects.select_for_update().get(pk=user_id, is_service_account=False)
    if user.role == User.Role.ADMIN and not is_admin(actor) and user.id != actor.id:
        raise PermissionDenied('Solo un administrador puede cambiar la contraseña de otro administrador.')
    _validate_password(password, user)
    user.set_password(password)
    user.save(update_fields=['password', 'updated_at'])
    return user


# --- Módulo de configuración: roles ------------------------------------------


def _role_queryset() -> QuerySet[StaffRole]:
    # `user_count` cuenta por código porque `User.role` es texto, no FK.
    from django.db.models import OuterRef, Subquery
    from django.db.models.functions import Coalesce

    users = (
        User.objects.filter(role=OuterRef('code'), is_service_account=False)
        .order_by()
        .values('role')
        .annotate(total=Count('id'))
        .values('total')
    )
    return StaffRole.objects.annotate(user_count=Coalesce(Subquery(users), 0))


def list_roles(include_inactive: bool = True) -> QuerySet[StaffRole]:
    queryset = _role_queryset()
    if not include_inactive:
        queryset = queryset.filter(is_active=True)
    return queryset.order_by('-is_system', 'name')


def get_role(role_id: int) -> StaffRole:
    return _role_queryset().get(pk=role_id)


def _slug_code(name: str) -> str:
    normalized = unicodedata.normalize('NFKD', name).encode('ascii', 'ignore').decode('ascii')
    code = re.sub(r'[^A-Za-z0-9]+', '_', normalized).strip('_').upper()
    return (code or 'ROL')[:32]


def _validate_role_permissions(permissions: list[str], actor: User) -> list[str]:
    unknown = sorted(set(permissions) - ALL_PERMISSIONS)
    if unknown:
        raise UserAdminError(f'Permisos desconocidos: {", ".join(unknown)}.')
    if not is_admin(actor):
        exceeding = set(permissions) - user_permissions(actor)
        if exceeding:
            raise PermissionDenied('No puedes otorgar a un rol permisos que tú no tienes.')
    return sorted(set(permissions))


@transaction.atomic
def create_role(payload: RoleIn, actor: User) -> StaffRole:
    permissions = _validate_role_permissions(payload.permissions, actor)
    base = _slug_code(payload.name)
    code = base
    suffix = 2
    while StaffRole.objects.filter(code=code).exists():
        code = f'{base}_{suffix}'
        suffix += 1
    role = StaffRole.objects.create(
        code=code,
        name=payload.name.strip(),
        description=payload.description.strip(),
        permissions=permissions,
        is_active=payload.is_active,
    )
    return get_role(role.id)


@transaction.atomic
def update_role(role_id: int, payload: RoleIn, actor: User) -> StaffRole:
    role = StaffRole.objects.select_for_update().get(pk=role_id)
    if role.code == User.Role.ADMIN:
        raise UserAdminError('El rol Administrador tiene siempre todos los permisos y no se edita.')
    permissions = _validate_role_permissions(payload.permissions, actor)
    if not payload.is_active and User.objects.filter(role=role.code, is_active=True).exists():
        raise UserAdminError('No se puede desactivar un rol que tiene usuarios activos. Reasígnalos primero.')
    role.name = payload.name.strip()
    role.description = payload.description.strip()
    role.permissions = permissions
    role.is_active = payload.is_active
    role.save()
    return get_role(role.id)


@transaction.atomic
def delete_role(role_id: int, actor: User) -> None:
    role = StaffRole.objects.select_for_update().get(pk=role_id)
    if role.is_system:
        raise UserAdminError('Los roles de sistema no se pueden borrar; puedes editar sus permisos.')
    if User.objects.filter(role=role.code).exists():
        raise UserAdminError('El rol tiene usuarios asignados. Reasígnalos antes de borrarlo.')
    if not is_admin(actor) and set(role.permissions) - user_permissions(actor):
        raise PermissionDenied('No puedes borrar un rol con permisos que tú no tienes.')
    role.delete()

