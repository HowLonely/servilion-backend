"""Fila de base de datos <-> diccionario JSON que viaja entre nodos.

Viajan todas las columnas concretas por su `attname` (las FK como `company_id`),
con el mismo ID en los dos lados (ver `SYNC_EDGE_ID_START`). No se usan los
schemas de la API a propósito: esos cambian con las pantallas, esto tiene que
replicar la fila exacta.

Es tolerante a versiones distintas: un nodo recién actualizado puede tener una
columna que el otro todavía no. Lo desconocido se ignora al aplicar y lo
faltante conserva el valor que ya tenía la fila.
"""

import datetime
import decimal
import uuid

from django.db import models

from sync.registry import SyncedModel


def _to_json(value):
    if isinstance(value, datetime.datetime | datetime.date | datetime.time):
        return value.isoformat()
    if isinstance(value, decimal.Decimal):
        return str(value)
    if isinstance(value, uuid.UUID):
        return str(value)
    return value


def synced_fields(spec: SyncedModel) -> list[models.Field]:
    return [f for f in spec.model._meta.concrete_fields if f.name not in spec.exclude and f.attname not in spec.exclude]


def serialize(spec: SyncedModel, instance: models.Model) -> dict:
    return {field.attname: _to_json(getattr(instance, field.attname)) for field in synced_fields(spec)}


def deserialize(spec: SyncedModel, data: dict) -> dict:
    values = {}
    for field in synced_fields(spec):
        if field.attname not in data:
            continue
        raw = data[field.attname]
        values[field.attname] = None if raw is None else field.to_python(raw)
    return values


def is_excluded(spec: SyncedModel, instance: models.Model) -> bool:
    return any(getattr(instance, key) == value for key, value in spec.exclude_filter.items())
