from datetime import datetime, timezone

import jwt
from django.conf import settings
from django.contrib.auth import authenticate

from authentication.models import User


class AuthError(Exception):
    """Credenciales, token o refresh token inválido/expirado."""


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
    if user is None or not user.is_active:
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
