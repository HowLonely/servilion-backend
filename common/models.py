from django.db import models


class TimeStampedModel(models.Model):
    """Base abstracta con auditoría de creación/edición.

    `updated_at` es la marca que usan los servicios de sincronización
    offline-first (app móvil) para resolver conflictos contra `last_sync`.
    """

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True, db_index=True)

    class Meta:
        abstract = True
