from django.conf import settings
from django.db import models

from common.models import TimeStampedModel
from companies.models import Company
from garments.models import GarmentType


class BatchStatus(models.TextChoices):
    """Estados del lote. Son menos que los de una guía a propósito.

    Una guía de trabajador tiene que pasar por revisión y empaque prenda por
    prenda porque el morral debe volver completo a su dueño. Un lote de
    hotelería no: entra a granel, se lava y se devuelve al mandante. Lo único
    que hay que controlar es cuánto entró y cuánto salió, y eso ocurre en dos
    momentos, no en cinco.
    """

    RECEIVED = 'RECIBIDO', 'Recibido'
    IN_PROCESS = 'EN_PROCESO', 'En proceso'
    DISPATCHED = 'DESPACHADO', 'Despachado'


class LinenBatch(TimeStampedModel):
    """Lote de lencería de hotelería: una carga a granel del campamento.

    Es deliberadamente un modelo aparte de `orders.LaundryOrder` y no una
    variante suya. Los dos servicios comparten la planta pero no el negocio:

    - Una guía es de una persona, va a una habitación y vuelve completa; su
      unidad de control es la prenda individual, pistoleada una por una.
    - Un lote no tiene dueño ni destino individual; su unidad de control es la
      cantidad por tipo de lencería, y lo que importa es la merma (cuántas
      sábanas entraron y cuántas volvieron), que en una guía no existe porque
      ahí lo que falta se busca o se repone.

    El sistema antiguo no tenía dónde poner esto y lo forzó dentro de las
    guías: creó 18 trabajadores falsos llamados "200 JUEGOS DE SABANAS" o
    "22 COBERTORES" —con RUT "0" y una habitación inventada— y abrió una guía
    por cada tipo de lencería. Los números de esos nombres eran la cantidad de
    la primera carga de 2017 y quedaron congelados nueve años, mientras las
    cargas reales llegaban a 944 unidades. Este modelo existe para no repetir
    ese apaño.
    """

    batch_number = models.CharField('N° de lote', max_length=20, unique=True)
    company = models.ForeignKey(Company, on_delete=models.PROTECT, related_name='linen_batches')
    # De qué campamento viene la carga. Es informativo: a diferencia de una
    # guía, el lote no se entrega en una pieza, vuelve al mandante completo.
    camp = models.ForeignKey(
        'camps.Camp', on_delete=models.SET_NULL, null=True, blank=True, related_name='linen_batches'
    )

    status = models.CharField(max_length=12, choices=BatchStatus.choices, default=BatchStatus.RECEIVED, db_index=True)
    received_at = models.DateTimeField('Recepción de la carga', db_index=True)
    promised_at = models.DateTimeField('Devolución comprometida', null=True, blank=True)
    dispatched_at = models.DateTimeField('Despacho de vuelta a faena', null=True, blank=True)

    # Peso total de la carga sucia. En hotelería el peso es la magnitud real del
    # trabajo (una carga de sábanas pesa 600 kg y una de cortinas 27), así que
    # se registra aunque el contrato hoy se cobre por prenda.
    weight_kg = models.DecimalField('Peso total (kg)', max_digits=8, decimal_places=2, null=True, blank=True)
    observations = models.TextField(blank=True)

    received_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name='+'
    )
    dispatched_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name='+'
    )
    # Quién firma la recepción en faena. En hotelería no hay QR de habitación ni
    # trabajador que reciba: la carga se entrega al encargado del mandante.
    received_by_client = models.CharField('Recibido en faena por', max_length=100, blank=True)

    class Meta:
        verbose_name = 'Lote de hotelería'
        verbose_name_plural = 'Lotes de hotelería'
        ordering = ['-received_at']
        indexes = [
            models.Index(fields=['status', 'received_at']),
            models.Index(fields=['company', 'status']),
        ]

    def __str__(self) -> str:
        return f'Lote {self.batch_number} - {self.company.name}'


class LinenBatchItem(models.Model):
    """Una línea del lote: un tipo de lencería con lo que entró y lo que salió.

    `quantity_in` se cuenta al recibir la carga sucia y `quantity_out` al
    despacharla limpia. La diferencia es la merma, que es el dato que el
    servicio de hotelería necesita y el de trabajadores no tiene: aquí no se
    "busca la prenda faltante" como en un morral, se asume pérdida y se informa
    al mandante.

    `quantity_out` es null mientras el lote no se ha contado de salida, para
    distinguir "todavía no se cuenta" de "salieron 0".
    """

    batch = models.ForeignKey(LinenBatch, on_delete=models.CASCADE, related_name='items')
    garment_type = models.ForeignKey(GarmentType, on_delete=models.PROTECT, related_name='+', null=True, blank=True)
    custom_name = models.CharField('Lencería fuera de catálogo', max_length=60, blank=True)
    quantity_in = models.PositiveIntegerField('Cantidad recibida')
    quantity_out = models.PositiveIntegerField('Cantidad devuelta', null=True, blank=True)
    weight_kg = models.DecimalField(
        'Peso de esta lencería (kg)', max_digits=8, decimal_places=2, null=True, blank=True
    )

    class Meta:
        verbose_name = 'Línea de lote'
        verbose_name_plural = 'Líneas de lote'
        constraints = [
            models.UniqueConstraint(fields=['batch', 'garment_type'], name='unique_garment_type_per_batch'),
            models.CheckConstraint(
                check=models.Q(garment_type__isnull=False) | ~models.Q(custom_name=''),
                name='batch_item_has_garment_type_or_custom_name',
            ),
        ]

    def __str__(self) -> str:
        return f'{self.quantity_in} x {self.display_name}'

    @property
    def display_name(self) -> str:
        return self.garment_type.name if self.garment_type_id else self.custom_name

    @property
    def shortage(self) -> int | None:
        """Merma de la línea. None mientras no se cuente la salida."""
        if self.quantity_out is None:
            return None
        return self.quantity_in - self.quantity_out
