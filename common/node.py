"""En qué nodo corre este backend: la nube (api.servilion.cl) o el servidor local de la planta.

El mismo código corre en los dos lados. Lo que cambia es quién puede escribir
qué (ver `sync/README.md`):

- El **servidor local** de la planta de Antofagasta es el único que opera el
  morral: pesaje, digitalización, empaque, despacho y despacho de hotelería. Es
  también el único que emite correlativos (`ref`, `HD-2026-0001`), porque dos
  emisores sin conexión entre sí terminarían repitiendo números.
- La **nube** recibe esa operación sincronizada, atiende a la app móvil de faena
  y al panel web, que desde aquí es de consulta y administración.
"""

from functools import wraps
from typing import Callable, TypeVar

from django.conf import settings

F = TypeVar('F', bound=Callable)

NODE_CLOUD = 'cloud'
NODE_EDGE = 'edge'


class PlantOnlyOperation(Exception):
    """La operación solo se hace en la planta, contra el servidor local."""


def is_edge() -> bool:
    return settings.SERVILION_NODE == NODE_EDGE


def plant_operations_allowed() -> bool:
    return settings.ALLOW_PLANT_OPERATIONS


def plant_only(handler: F) -> F:
    """Restringe un endpoint a la operación de planta (servidor local).

    En la nube estas operaciones escribirían en paralelo con la planta: dos
    lugares emitiendo refs, cerrando el mismo morral o despachándolo. Por eso
    la nube las rechaza con un mensaje claro en vez de aceptarlas y dejar que la
    sincronización descubra el choque después.
    """

    @wraps(handler)
    def wrapper(request, *args, **kwargs):
        if not plant_operations_allowed():
            raise PlantOnlyOperation(
                'Esta operación se hace solo en la planta, con Servilion Desktop '
                'conectado al servidor local.'
            )
        return handler(request, *args, **kwargs)

    return wrapper  # type: ignore[return-value]
