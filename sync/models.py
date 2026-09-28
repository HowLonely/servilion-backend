from django.conf import settings
from django.db import models
from django.db.models.functions import Now

from common.models import TimeStampedModel


class ChangeLog(models.Model):
    """Cola de cambios pendientes de replicar al otro nodo.

    No la escribe Django: la llenan triggers de PostgreSQL instalados en cada
    tabla sincronizada (ver `sync/triggers.py`). Así ningún camino de escritura
    se escapa —`bulk_create`, `QuerySet.update()`, SQL crudo— y ninguna
    aplicación tiene que acordarse de avisar que cambió algo.

    Guarda qué fila cambió, no cómo quedó: al enviar se lee el estado actual.
    Varios cambios seguidos a la misma guía viajan como uno solo.

    - En el servidor local es el outbox: se envía a la nube y se borra al
      recibir el acuse.
    - En la nube es el feed que el servidor local lee con un cursor.

    Lo que escribe la propia sincronización no se anota (el trigger lo omite
    con la variable de sesión `servilion.sync_applying`), para que un cambio
    recibido no rebote de vuelta a su origen.
    """

    table = models.CharField(max_length=64)
    row_id = models.BigIntegerField()
    op = models.CharField(max_length=1)  # 'U' alta o modificación, 'D' borrado
    created_at = models.DateTimeField(db_default=Now())

    class Meta:
        db_table = 'sync_changelog'
        verbose_name = 'Cambio pendiente'
        verbose_name_plural = 'Cambios pendientes'
        indexes = [models.Index(fields=['created_at'], name='sync_changelog_created_idx')]

    def __str__(self) -> str:
        return f'{self.op} {self.table}#{self.row_id}'


class Node(TimeStampedModel):
    """Servidor local registrado en la nube (lado nube).

    Se autentica con un token propio (`Authorization: Bearer srvnode_...`), no
    con la cuenta de una persona: sincroniza aunque nadie haya iniciado sesión
    en la planta. El token se guarda solo como hash.

    `service_user` es la identidad con la que el servidor local consulta la API
    normal de la nube —el histórico de más de 90 días que ya no guarda—; es una
    cuenta técnica que no inicia sesión ni aparece en el módulo de usuarios.
    """

    name = models.CharField(max_length=60, unique=True)
    token_hash = models.CharField(max_length=64, unique=True)
    service_user = models.OneToOneField(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name='sync_node'
    )
    is_active = models.BooleanField(default=True)
    # Hasta dónde leyó el feed de la nube: lo anterior ya se puede borrar.
    pull_cursor = models.BigIntegerField(default=0)
    last_push_at = models.DateTimeField(null=True, blank=True)
    last_pull_at = models.DateTimeField(null=True, blank=True)
    # Cambios que el servidor local dijo tener pendientes en su último envío.
    reported_pending = models.PositiveIntegerField(default=0)

    class Meta:
        verbose_name = 'Servidor local'
        verbose_name_plural = 'Servidores locales'
        ordering = ['name']

    def __str__(self) -> str:
        return self.name


class SyncState(models.Model):
    """Estado del proceso de sincronización, clave/valor (ambos nodos)."""

    key = models.CharField(max_length=40, primary_key=True)
    value = models.JSONField(null=True, blank=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = 'Estado de sincronización'
        verbose_name_plural = 'Estado de sincronización'

    def __str__(self) -> str:
        return f'{self.key}={self.value}'


class SyncIssue(models.Model):
    """Algo que la sincronización resolvió sola pero conviene que alguien revise.

    La replicación nunca se detiene por una fila: la resuelve con una regla fija
    (la misma en los dos nodos, para que terminen iguales) y deja constancia
    aquí. Casos:

    - DESCARTADO: la misma ficha se editó en la web y en la planta; ganó la
      edición más reciente y la otra se descartó.
    - RENOMBRADO: se crearon dos fichas con el mismo nombre único (ej. el mismo
      usuario) en ambos lados sin conexión; a la segunda se le agregó un sufijo.
    - DUPLICADO: se definió dos veces el mismo precio de prenda; quedó uno.
    - ERROR: la fila no se pudo aplicar.
    """

    class Kind(models.TextChoices):
        DISCARDED = 'DESCARTADO', 'Edición descartada'
        RENAMED = 'RENOMBRADO', 'Nombre duplicado renombrado'
        DUPLICATE = 'DUPLICADO', 'Registro duplicado eliminado'
        ERROR = 'ERROR', 'Error al aplicar'

    kind = models.CharField(max_length=12, choices=Kind.choices)
    table = models.CharField(max_length=64)
    row_id = models.BigIntegerField()
    detail = models.CharField(max_length=255)
    payload = models.JSONField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)
    resolved_at = models.DateTimeField(null=True, blank=True)
    resolved_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name='+'
    )

    class Meta:
        verbose_name = 'Incidencia de sincronización'
        verbose_name_plural = 'Incidencias de sincronización'
        ordering = ['-created_at']

    def __str__(self) -> str:
        return f'{self.kind} {self.table}#{self.row_id}'
