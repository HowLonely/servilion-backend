"""Qué tablas se replican entre la nube y el servidor local, y cómo.

El orden de `SYNCED_MODELS` es el de dependencias: una fila se aplica después
de las filas a las que apunta (catálogo antes que guías, guía antes que sus
prendas). Los borrados se aplican en el orden inverso.

Políticas:

- LWW (last-write-wins): catálogo que se edita en la web y en la planta.
  Gana la versión con `updated_at` más reciente; la otra queda como incidencia.
- ORDER: la guía. La planta es dueña de casi todo, y la nube solo agrega hitos
  de faena (recepción, entrega) que registra la app móvil. Se fusiona campo a
  campo: los hitos nunca se pierden y el estado nunca retrocede.
- MERGE: filas que un lado crea y el otro solo completa (pistoleos de faena):
  un valor vacío entrante no borra uno existente.
- UPSERT: filas que solo escribe un lado (pesajes, etiquetas, correlativos).
  La versión entrante reemplaza a la existente.

Si se agrega una tabla aquí hay que instalarle el trigger con una migración
(ver `sync/triggers.py`).
"""

from dataclasses import dataclass, field
from functools import cached_property

from django.apps import apps
from django.db import models

LWW = 'lww'
ORDER = 'order'
MERGE = 'merge'
UPSERT = 'upsert'


@dataclass(frozen=True)
class SyncedModel:
    label: str
    policy: str
    # Restricciones únicas "de negocio" que dos nodos pueden violar al crear
    # sin conexión (ej. el mismo nombre de usuario). Ver `apply.resolve_unique`.
    unique_keys: tuple[tuple[str, ...], ...] = ()
    # Campo al que se le agrega un sufijo si hay choque. None: el duplicado se
    # descarta (solo para filas que nadie más referencia, como un precio).
    rename_field: str | None = None
    # Campos que no viajan.
    exclude: tuple[str, ...] = ()
    # Filas que nunca salen del nodo (ej. cuentas técnicas).
    exclude_filter: dict = field(default_factory=dict)
    # El servidor local solo guarda una ventana reciente de estas filas.
    windowed: bool = False

    @cached_property
    def model(self) -> type[models.Model]:
        return apps.get_model(self.label)

    @property
    def table(self) -> str:
        return self.model._meta.db_table


SYNCED_MODELS: tuple[SyncedModel, ...] = (
    # --- Usuarios y roles ---
    SyncedModel('authentication.StaffRole', LWW, unique_keys=(('code',),), rename_field='code'),
    SyncedModel(
        'authentication.User', LWW,
        unique_keys=(('username',),), rename_field='username',
        exclude=('last_login',),
        exclude_filter={'is_service_account': True},
    ),
    # --- Catálogo ---
    SyncedModel('camps.Faena', LWW, unique_keys=(('name',),), rename_field='name'),
    SyncedModel('camps.Camp', LWW, unique_keys=(('faena_id', 'name'),), rename_field='name'),
    SyncedModel('camps.Room', LWW, unique_keys=(('camp_id', 'number'),), rename_field='number'),
    SyncedModel('garments.GarmentType', LWW, unique_keys=(('code',),), rename_field='code'),
    SyncedModel('companies.Client', LWW, unique_keys=(('name',),), rename_field='name'),
    SyncedModel('companies.Company', LWW, unique_keys=(('name',),), rename_field='name'),
    SyncedModel('companies.ClientGarmentPrice', LWW, unique_keys=(('client_id', 'garment_type_id'),)),
    SyncedModel('workers.Worker', LWW, unique_keys=(('company_id', 'badge_code'),), rename_field='badge_code'),
    SyncedModel('weighing.WeighingSettings', LWW),
    # --- Correlativos: solo los emite el servidor local ---
    SyncedModel('orders.ReferenceCounter', UPSERT),
    SyncedModel('hospitality.DispatchCounter', UPSERT),
    # --- Operación de planta ---
    SyncedModel(
        'orders.LaundryOrder', ORDER,
        unique_keys=(('order_number',),), rename_field='order_number',
        windowed=True,
    ),
    SyncedModel('orders.OrderItem', UPSERT, windowed=True),
    SyncedModel('weighing.WeighIn', UPSERT, windowed=True),
    SyncedModel('weighing.WeighLabel', UPSERT, windowed=True),
    SyncedModel('orders.MissingItemResolution', UPSERT, windowed=True),
    SyncedModel('orders.OrderStatusHistory', UPSERT, windowed=True),
    SyncedModel('orders.SiteScan', MERGE, windowed=True),
    SyncedModel('orders.SyncConflict', UPSERT, windowed=True),
    # --- Hotelería: pocas filas, se conservan completas (los saldos se
    # calculan sumando todos los movimientos desde el último conteo) ---
    SyncedModel('hospitality.LinenMovement', LWW),
    SyncedModel('hospitality.LinenMovementLine', UPSERT),
)

def by_table() -> dict[str, SyncedModel]:
    return {spec.table: spec for spec in SYNCED_MODELS}


def order_index() -> dict[str, int]:
    return {spec.table: index for index, spec in enumerate(SYNCED_MODELS)}


def get_spec(table: str) -> SyncedModel:
    try:
        return by_table()[table]
    except KeyError:
        raise ValueError(f'La tabla {table} no se sincroniza.') from None
