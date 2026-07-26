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


def require_admin() -> Callable[[F], F]:
    """Reserva un handler para ADMIN.

    Cubre la administración del catálogo y del dinero (clientes, empresas,
    trabajadores, prendas, facturación y conflictos de sincronización), que es
    justo lo que separa a ADMIN de SUPERVISOR. Existe como decorador propio
    porque `require_roles(User.Role.ADMIN)` se apoya en que ADMIN atraviesa
    toda restricción, y eso se lee como un descuido en vez de una decisión.
    """
    return require_roles(User.Role.ADMIN)
