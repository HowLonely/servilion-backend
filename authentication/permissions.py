from functools import wraps
from typing import Callable, TypeVar

from authentication.models import User

F = TypeVar('F', bound=Callable)


class PermissionDenied(Exception):
    """El staff autenticado no tiene el rol necesario para esta operación."""


def user_has_role(user: User, *roles: str) -> bool:
    """ADMIN atraviesa cualquier restricción de rol; el resto debe estar listado."""
    return user.role == User.Role.ADMIN or user.role in roles


def require_roles(*roles: str) -> Callable[[F], F]:
    """Restringe un handler de la API a ciertos roles de staff.

    Se aplica en `api.py` (no en `services.py`) porque es una regla de acceso
    HTTP, no de negocio: los servicios siguen siendo funciones puras invocables
    desde Celery, comandos de management o la importación de datos legados.
    """

    def decorator(handler: F) -> F:
        @wraps(handler)
        def wrapper(request, *args, **kwargs):
            if not user_has_role(request.auth, *roles):
                raise PermissionDenied(
                    f'Tu rol ({request.auth.get_role_display()}) no tiene permiso para esta acción.'
                )
            return handler(request, *args, **kwargs)

        return wrapper  # type: ignore[return-value]

    return decorator
