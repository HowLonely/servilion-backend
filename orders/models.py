import uuid

from django.conf import settings
from django.db import models

from common.models import TimeStampedModel
from companies.models import Company
from garments.models import GarmentType
from workers.models import Worker


# El correlativo semanal del `ref` arranca en 1000 cada semana (ver ReferenceCounter).
REFERENCE_SEQUENCE_START = 1000


class OrderStatus(models.TextChoices):
    # RECIBIDA cubre desde que se digitaliza la guía hasta el primer pistoleo
    # de empaque: no existe un evento físico propio de "en lavado" (el lavado
    # ocurre sin que el sistema lo registre), así que ese estado intermedio se
    # eliminó — antes obligaba a un clic manual que no representaba nada real.
    RECEIVED = 'RECIBIDA', 'Recibida'
    QUALITY_CHECK = 'EN_REVISION', 'En revisión'
    INCOMPLETE = 'INCOMPLETA', 'Incompleta'
    COMPLETED = 'COMPLETADA', 'Completada'
    # DESPACHADA también se eliminó: no había ningún pistoleo que representara
    # "salió de planta", así que el concepto queda absorbido por COMPLETADA. El
    # próximo hito con evidencia real es la recepción en faena (ver SiteScan).
    DELIVERED = 'ENTREGADA', 'Entregada'


class LaundryOrder(TimeStampedModel):
    """Guía de lavandería: unidad de trabajo entre la recepción de ropa sucia y su entrega.

    `client_uuid` es la clave de idempotencia para la sincronización offline-first
    de la app móvil: se genera en el dispositivo antes de tener conectividad y
    permite reintentar el envío del lote sin duplicar la guía en el servidor.
    """

    client_uuid = models.UUIDField(default=uuid.uuid4, unique=True, editable=False)
    # Null = el trabajador no escribió un número de OT en el papel físico (el
    # digitador sigue tipeando "0" como siempre; el servicio lo normaliza a
    # None, ver `services.normalize_order_number`). Postgres permite múltiples
    # NULL en una columna unique sin chocar entre sí, así que no hace falta
    # inventar un valor de relleno: la guía se sigue identificando por `reference`.
    order_number = models.CharField('N° OT', max_length=20, unique=True, null=True, blank=True)
    ticket_number = models.CharField(max_length=20, blank=True)

    worker = models.ForeignKey(Worker, on_delete=models.PROTECT, related_name='orders')
    company = models.ForeignKey(Company, on_delete=models.PROTECT, related_name='orders')

    shift = models.CharField('Turno', max_length=10, blank=True)
    status = models.CharField(max_length=15, choices=OrderStatus.choices, default=OrderStatus.RECEIVED, db_index=True)
    garment_count = models.PositiveIntegerField(default=0)
    weight_kg = models.DecimalField(max_digits=6, decimal_places=2, null=True, blank=True)

    # Hitos del flujo real (FLUJO_NEGOCIO.md §4). `site_received_at` y
    # `laundry_received_at` son los `recepcion` / `rlavanderia` del sistema
    # legado: eventos anteriores al estado RECIBIDA, por eso son timestamps
    # independientes y no estados de la máquina.
    received_at = models.DateTimeField(db_index=True)
    site_received_at = models.DateTimeField('Recepción ropa sucia en faena', null=True, blank=True)
    laundry_received_at = models.DateTimeField('Recepción ropa sucia en lavandería', null=True, blank=True)
    packed_at = models.DateTimeField('Empaque validado por pistoleo', null=True, blank=True)
    promised_at = models.DateTimeField(null=True, blank=True)
    # Se fija la primera vez que el cierre de empaque detecta un faltante.
    # Junto con `completed_at` permite medir cuánto demoró resolverse.
    incomplete_at = models.DateTimeField('Guía marcada incompleta al empacar', null=True, blank=True)
    completed_at = models.DateTimeField(null=True, blank=True)
    delivered_at = models.DateTimeField(null=True, blank=True)

    observations = models.TextField(blank=True)
    # `reference` (ref) y `control_code` se pistolean tanto como el n° de OT,
    # por eso van indexados: son entradas válidas del buscador de escaneo.
    reference = models.CharField(max_length=20, blank=True, db_index=True)
    control_code = models.CharField(max_length=20, blank=True, db_index=True)
    photo_key = models.CharField('Objeto S3 de la foto', max_length=255, blank=True)

    received_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name='+'
    )
    reviewed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name='+'
    )
    site_received_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name='+'
    )
    packed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name='+'
    )
    delivered_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name='+'
    )

    class Meta:
        verbose_name = 'Guía'
        verbose_name_plural = 'Guías'
        ordering = ['-received_at']
        indexes = [
            models.Index(fields=['status', 'received_at']),
            models.Index(fields=['company', 'status']),
            # Índices para la detección de guías atascadas de la Torre de Control
            # (reportería en tiempo real): cada estado se mide contra su propio
            # timestamp de entrada, así que el rango sobre esas columnas debe ser
            # indexado. Ver ai_context/REPORTES_API_CONTRACT.md §3.
            models.Index(fields=['incomplete_at'], name='ord_incomplete_at_idx'),
            models.Index(fields=['completed_at'], name='ord_completed_at_idx'),
            models.Index(fields=['delivered_at'], name='ord_delivered_at_idx'),
        ]

    def __str__(self) -> str:
        return f'Guía {self.order_number} - {self.worker.full_name}'


class OrderItem(models.Model):
    """Detalle normalizado de prendas de una guía (reemplaza el campo de texto libre legado).

    `garment_type` es opcional porque la OT física admite prendas fuera del
    catálogo preimpreso: el trabajador las escribe a mano y Antofagasta debe
    poder digitalizarlas igual (FLUJO_NEGOCIO.md §7). En ese caso el nombre
    viene en `custom_name`.

    `scanned_quantity` acumula el pistoleo del paso 6 (empaque): el sistema
    valida que el morral limpio quede completo respecto de lo declarado.
    """

    order = models.ForeignKey(LaundryOrder, on_delete=models.CASCADE, related_name='items')
    garment_type = models.ForeignKey(GarmentType, on_delete=models.PROTECT, related_name='+', null=True, blank=True)
    custom_name = models.CharField('Prenda fuera de catálogo', max_length=60, blank=True)
    quantity = models.PositiveIntegerField()
    scanned_quantity = models.PositiveIntegerField('Prendas pistoleadas al empacar', default=0)

    class Meta:
        verbose_name = 'Detalle de guía'
        verbose_name_plural = 'Detalles de guía'
        constraints = [
            # Solo aplica a prendas del catálogo: en Postgres los NULL no
            # colisionan entre sí, así que una guía puede tener varias líneas
            # fuera de catálogo con nombres distintos.
            models.UniqueConstraint(fields=['order', 'garment_type'], name='unique_garment_type_per_order'),
            models.CheckConstraint(
                check=models.Q(garment_type__isnull=False) | ~models.Q(custom_name=''),
                name='item_has_garment_type_or_custom_name',
            ),
        ]

    def __str__(self) -> str:
        return f'{self.quantity} x {self.display_name}'

    @property
    def display_name(self) -> str:
        return self.garment_type.name if self.garment_type_id else self.custom_name


class MissingItemResolution(models.Model):
    """Registra cómo se resolvió una prenda que faltó en el empaque (guía INCOMPLETA).

    Hay dos caminos: la prenda aparece y se pistolea con su propio código
    (`ENCONTRADA`), o no aparece y hay que reponerla comprando una nueva
    (`COMPRADA`, con su costo). Se guarda cada resolución por separado —no solo
    el resultado final— para poder sacar estadísticas de cuánto se pierde en
    prendas repuestas y cuánto demora encontrarlas.
    """

    class ResolutionType(models.TextChoices):
        FOUND = 'ENCONTRADA', 'Encontrada'
        PURCHASED = 'COMPRADA', 'Comprada'

    order = models.ForeignKey(LaundryOrder, on_delete=models.CASCADE, related_name='missing_item_resolutions')
    item = models.ForeignKey(OrderItem, on_delete=models.CASCADE, related_name='resolutions')
    resolution_type = models.CharField(max_length=15, choices=ResolutionType.choices)
    quantity = models.PositiveIntegerField(default=1)
    purchase_cost = models.DecimalField(max_digits=10, decimal_places=2, null=True, blank=True)
    resolved_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name='+'
    )
    resolved_at = models.DateTimeField(auto_now_add=True)
    note = models.CharField(max_length=255, blank=True)

    class Meta:
        verbose_name = 'Resolución de prenda faltante'
        verbose_name_plural = 'Resoluciones de prenda faltante'
        ordering = ['-resolved_at']
        # Índices para el panel de Calidad e Incidencias (reportería): se agrega
        # por período de resolución y por tipo (encontrada/comprada).
        indexes = [
            models.Index(fields=['resolved_at'], name='mir_resolved_at_idx'),
            models.Index(fields=['resolution_type'], name='mir_res_type_idx'),
        ]
        constraints = [
            models.CheckConstraint(
                check=~models.Q(resolution_type='COMPRADA') | models.Q(purchase_cost__isnull=False),
                name='purchase_requires_cost',
            ),
        ]

    def __str__(self) -> str:
        return f'{self.get_resolution_type_display()} - {self.item.display_name} x{self.quantity}'


class ReferenceCounter(models.Model):
    """Correlativo semanal del `ref` por prefijo de faena/empresa.

    El negocio resetea el número a 1000 cada semana (FLUJO_NEGOCIO.md §4, paso
    4), así que la unicidad se define por (prefijo, año ISO, semana ISO) y no
    por un autoincremental global.
    """

    prefix = models.CharField(max_length=3)
    iso_year = models.PositiveSmallIntegerField()
    iso_week = models.PositiveSmallIntegerField()
    last_number = models.PositiveIntegerField(default=REFERENCE_SEQUENCE_START)

    class Meta:
        verbose_name = 'Correlativo de ref'
        verbose_name_plural = 'Correlativos de ref'
        constraints = [
            models.UniqueConstraint(fields=['prefix', 'iso_year', 'iso_week'], name='unique_reference_counter_week'),
        ]

    def __str__(self) -> str:
        return f'{self.prefix} {self.iso_year}-W{self.iso_week}: {self.last_number}'


class SiteScan(models.Model):
    """Pistoleo registrado en faena por la app PEÑON (FLUJO_NEGOCIO.md §4, pasos 2 y 8).

    El pistoleo de ropa sucia (paso 2) ocurre ANTES de que la guía exista en el
    sistema: la digitalización recién pasa en Antofagasta (paso 4). Por eso el
    escaneo se guarda por número de OT con `order` en nulo y se enlaza a la guía
    cuando esta se digitaliza.
    """

    class Kind(models.TextChoices):
        DIRTY_IN = 'RECEPCION_SUCIA', 'Recepción ropa sucia en faena'
        CLEAN_IN = 'RECEPCION_LIMPIA', 'Recepción ropa limpia en faena'
        DELIVERY = 'ENTREGA', 'Entrega en habitación'

    kind = models.CharField(max_length=20, choices=Kind.choices)
    scanned_code = models.CharField('Código pistoleado', max_length=30, db_index=True)
    order = models.ForeignKey(
        'orders.LaundryOrder', on_delete=models.CASCADE, related_name='site_scans', null=True, blank=True
    )
    scanned_at = models.DateTimeField(db_index=True)
    scanned_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, related_name='+')
    note = models.CharField(max_length=255, blank=True)

    class Meta:
        verbose_name = 'Pistoleo en faena'
        verbose_name_plural = 'Pistoleos en faena'
        ordering = ['-scanned_at']
        indexes = [models.Index(fields=['kind', 'scanned_at'])]

    def __str__(self) -> str:
        return f'{self.get_kind_display()} - {self.scanned_code}'


class SyncConflict(models.Model):
    """Cambio entrante de la app móvil descartado por ser más antiguo que el del servidor.

    La política de sync es last-write-wins por `updated_at`, pero descartar en
    silencio es justamente el riesgo señalado en `AI_LOGS/prompt_19_07_26.md`:
    con turnos rotativos dos dispositivos pueden editar la misma guía offline
    durante días. Se deja registro para que un supervisor lo revise.
    """

    order = models.ForeignKey(LaundryOrder, on_delete=models.CASCADE, related_name='sync_conflicts')
    client_uuid = models.UUIDField()
    device_updated_at = models.DateTimeField('Versión que traía el dispositivo')
    server_updated_at = models.DateTimeField('Versión que tenía el servidor')
    discarded_payload = models.JSONField('Datos descartados')
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)
    resolved_at = models.DateTimeField(null=True, blank=True)
    resolved_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, related_name='+')
    resolution_note = models.CharField(max_length=255, blank=True)

    class Meta:
        verbose_name = 'Conflicto de sincronización'
        verbose_name_plural = 'Conflictos de sincronización'
        ordering = ['-created_at']

    def __str__(self) -> str:
        return f'Conflicto guía {self.order_id} ({self.client_uuid})'


class OrderStatusHistory(models.Model):
    """Auditoría de cada cambio de estado de una guía."""

    order = models.ForeignKey(LaundryOrder, on_delete=models.CASCADE, related_name='status_history')
    previous_status = models.CharField(max_length=15, choices=OrderStatus.choices)
    new_status = models.CharField(max_length=15, choices=OrderStatus.choices)
    changed_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, related_name='+')
    changed_at = models.DateTimeField(auto_now_add=True)
    note = models.CharField(max_length=255, blank=True)

    class Meta:
        verbose_name = 'Historial de estado'
        verbose_name_plural = 'Historial de estados'
        ordering = ['-changed_at']
