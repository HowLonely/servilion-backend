from dataclasses import dataclass
from functools import wraps
from typing import Callable, TypeVar

from authentication.models import StaffRole, User

F = TypeVar('F', bound=Callable)


class PermissionDenied(Exception):
    """El staff autenticado no tiene el permiso necesario para esta operación."""


@dataclass(frozen=True)
class Permission:
    code: str
    label: str
    group: str
    description: str


class Perm:
    """Códigos de permiso. Cada uno lo exige al menos un endpoint.

    Son la unidad que se asigna a un rol desde el módulo de configuración. Si se
    agrega uno, va también en `PERMISSIONS` (con su texto para la pantalla) y en
    los roles de sistema que deban tenerlo (`SYSTEM_ROLES`, más una migración de
    datos para las bases que ya existen).
    """

    WEIGHING = 'weighing.operate'
    DIGITIZE = 'orders.digitize'
    PACK = 'orders.pack'
    DISPATCH = 'orders.dispatch'
    LINEN_DISPATCH = 'hospitality.dispatch'
    LINEN_VIEW = 'hospitality.view'
    HISTORY = 'history.view'

    FIELD_ORDERS = 'field.orders'
    FIELD_LINEN = 'field.hospitality'

    REPORTS = 'reports.view'
    CATALOG = 'catalog.manage'
    WORKERS = 'workers.manage'
    LINEN_MANAGE = 'hospitality.manage'
    SETTINGS = 'settings.manage'
    USERS = 'users.manage'
    SYNC = 'sync.manage'


PLANT = 'Estaciones de planta'
FIELD = 'Faena (app móvil)'
ADMINISTRATION = 'Administración'

PERMISSIONS: tuple[Permission, ...] = (
    Permission(Perm.WEIGHING, 'Pesaje y etiquetado', PLANT,
               'Pesar el morral sucio, imprimir sus etiquetas y anular pesajes.'),
    Permission(Perm.DIGITIZE, 'Digitalizar OT', PLANT,
               'Pasar al sistema la OT física que llega con el morral.'),
    Permission(Perm.PACK, 'Empaque y revisión', PLANT,
               'Pistolear el morral limpio, cerrarlo y resolver prendas faltantes.'),
    Permission(Perm.DISPATCH, 'Despacho de morrales', PLANT,
               'Pistolear la boleta de un morral cerrado para sacarlo de planta.'),
    Permission(Perm.LINEN_DISPATCH, 'Despacho de lencería', PLANT,
               'Despachar lencería limpia de hotelería a la faena del cliente.'),
    Permission(Perm.LINEN_VIEW, 'Saldos de lencería', PLANT,
               'Consultar dónde está la lencería de cada cliente de hotelería.'),
    Permission(Perm.HISTORY, 'Consultar histórico', PLANT,
               'Buscar guías y trabajadores anteriores.'),
    Permission(Perm.FIELD_ORDERS, 'Recepción y entrega en faena', FIELD,
               'Recibir morrales en faena y registrar la entrega en habitación.'),
    Permission(Perm.FIELD_LINEN, 'Reparto y retiro de lencería', FIELD,
               'Repartir lencería a los campamentos y retirar la sucia.'),
    Permission(Perm.REPORTS, 'Panel y reportería', ADMINISTRATION,
               'Ver el panel web, el listado de OT y la torre de control.'),
    Permission(Perm.CATALOG, 'Catálogo', ADMINISTRATION,
               'Clientes, empresas, prendas, precios, faenas, campamentos y habitaciones.'),
    Permission(Perm.WORKERS, 'Trabajadores', ADMINISTRATION,
               'Crear, editar y desactivar trabajadores.'),
    Permission(Perm.LINEN_MANAGE, 'Inventario de lencería', ADMINISTRATION,
               'Conteo de inventario y anulación de movimientos de lencería.'),
    Permission(Perm.SETTINGS, 'Configuración de pesaje', ADMINISTRATION,
               'Cupo mensual de cargos express.'),
    Permission(Perm.USERS, 'Usuarios y roles', ADMINISTRATION,
               'Crear usuarios, asignarles un rol y definir qué permite cada rol.'),
    Permission(Perm.SYNC, 'Sincronización', ADMINISTRATION,
               'Conflictos de sincronización y estado del servidor local.'),
)

ALL_PERMISSIONS: frozenset[str] = frozenset(permission.code for permission in PERMISSIONS)

# Los cinco roles de siempre, con los permisos que equivalen a lo que cada uno
# podía hacer cuando los roles eran fijos. ADMIN no lleva lista: tiene todos.
SYSTEM_ROLES: dict[str, tuple[str, str, list[str]]] = {
    User.Role.ADMIN: ('Administrador', 'Acceso total al sistema.', []),
    User.Role.SUPERVISOR: (
        'Supervisor',
        'Toda la operación de planta y de faena, sin administrar el catálogo.',
        [
            Perm.WEIGHING, Perm.DIGITIZE, Perm.PACK, Perm.DISPATCH, Perm.LINEN_VIEW,
            Perm.HISTORY, Perm.FIELD_ORDERS, Perm.FIELD_LINEN, Perm.REPORTS,
        ],
    ),
    User.Role.PESAJE: ('Pesaje', 'Báscula de recepción de la planta.', [Perm.WEIGHING]),
    User.Role.DIGITADOR_OT: (
        'Digitador de OT', 'Digitalización de la OT física.', [Perm.DIGITIZE, Perm.HISTORY]
    ),
    User.Role.DIGITADOR_EMPAQUE: (
        'Digitador de Empaque',
        'Empaque, despacho de morrales y despacho de lencería.',
        [Perm.PACK, Perm.DISPATCH, Perm.LINEN_DISPATCH, Perm.LINEN_VIEW],
    ),
}


def role_permissions(role_code: str) -> set[str]:
    """Permisos que otorga un rol. ADMIN siempre todos; un rol inexistente o inactivo, ninguno."""
    if role_code == User.Role.ADMIN:
        return set(ALL_PERMISSIONS)
    role = StaffRole.objects.filter(code=role_code).only('permissions', 'is_active').first()
    if role is None:
        # Un rol de sistema sin fila (base recién creada, servidor local antes
        # de su primera sincronización) conserva sus permisos de siempre.
        defaults = SYSTEM_ROLES.get(role_code)
        return set(defaults[2]) if defaults else set()
    if not role.is_active:
        return set()
    return {code for code in role.permissions if code in ALL_PERMISSIONS}


def user_permissions(user: User) -> set[str]:
    """Permisos efectivos del usuario, memorizados en la instancia por request."""
    cached = getattr(user, '_permission_cache', None)
    if cached is None:
        cached = role_permissions(user.role)
        user._permission_cache = cached
    return cached


def user_has_permission(user: User, *codes: str) -> bool:
    """True si el usuario tiene al menos uno de los permisos indicados."""
    permissions = user_permissions(user)
    return any(code in permissions for code in codes)


def is_admin(user: User) -> bool:
    return user.role == User.Role.ADMIN


def require_permission(*codes: str) -> Callable[[F], F]:
    """Restringe un handler de la API a quien tenga alguno de estos permisos.

    Se aplica en `api.py` (no en `services.py`) porque es una regla de acceso
    HTTP, no de negocio: los servicios siguen siendo funciones puras invocables
    desde comandos de management, la sincronización o la importación legada.
    """

    def decorator(handler: F) -> F:
        @wraps(handler)
        def wrapper(request, *args, **kwargs):
            if not user_has_permission(request.auth, *codes):
                raise PermissionDenied(
                    f'Tu rol ({request.auth.role_name}) no tiene permiso para esta acción.'
                )
            return handler(request, *args, **kwargs)

        return wrapper  # type: ignore[return-value]

    return decorator
