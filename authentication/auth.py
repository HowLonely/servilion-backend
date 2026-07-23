from ninja.security import HttpBearer

from authentication.models import User
from authentication.services import AuthError, get_user_from_token


class JWTAuth(HttpBearer):
    """Autenticación stateless vía cabecera `Authorization: Bearer <token>`."""

    def authenticate(self, request, token: str) -> User | None:
        try:
            user = get_user_from_token(token, expected_type='access')
        except AuthError:
            return None
        request.user = user
        return user


def has_role(request, *roles: str) -> bool:
    """Verifica dentro de un handler si el staff autenticado tiene alguno de estos roles."""
    return request.auth.role in roles
