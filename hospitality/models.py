import uuid

from django.conf import settings
from django.db import models

from common.models import TimeStampedModel
from companies.models import Company
from garments.models import GarmentType


class LinenLocation(models.TextChoices):
    """Dónde puede estar la lencería de un cliente de hotelería.

    No es una tabla: los campamentos ya existen (`camps.Camp`) y las otras dos
    ubicaciones son una por cliente. Sirve para nombrar las filas del saldo.
    """

    SERVILION = 'SERVILION', 'En poder de Servilion'
    FAENA = 'BODEGA_FAENA', 'Por repartir en faena'
    CAMP = 'CAMPAMENTO', 'Campamento'


class LinenMovement(TimeStampedModel):
    """Un movimiento de lencería de hotelería: el stock rotativo del cliente.

    Reemplaza al lote (`LinenBatch`), que modelaba una carga que iba y volvía
    entera. La operación real no es así: la lencería es un stock del cliente que
    rota entre la planta y sus campamentos, y lo que sale limpio hacia un
    campamento no es lo mismo que llegó sucio de él. Por eso ya no se controla
    "cuánto volvió de esta carga", sino dónde está cada pieza:

        Servilion --DESPACHO--> bodega de faena --REPARTO--> campamento
            ^                                                    |
            +------------------------RETIRO (sucio)--------------+

    El saldo de cada ubicación no se guarda: se calcula desde estos movimientos
    (ver `services.compute_balances`). Un movimiento no se edita ni se borra;
    si estuvo mal, se anula y queda en el historial.

    El CONTEO es distinto a los demás: no mueve lencería, fija cuánto hay en un
    lugar. Sirve a la vez de carga inicial y de reajuste.
    """

    class Kind(models.TextChoices):
        DISPATCH = 'DESPACHO', 'Despacho a faena'
        DISTRIBUTION = 'REPARTO', 'Reparto a campamento'
        COLLECTION = 'RETIRO', 'Retiro de sucio'
        COUNT = 'CONTEO', 'Conteo de inventario'

    # Clave de idempotencia de la app móvil, igual que en las entregas: el
    # reparto y el retiro se registran sin señal y se reintentan al sincronizar.
    client_uuid = models.UUIDField(default=uuid.uuid4, unique=True, editable=False)
    kind = models.CharField('Tipo', max_length=10, choices=Kind.choices, db_index=True)
    company = models.ForeignKey(Company, on_delete=models.PROTECT, related_name='linen_movements')
    # Obligatorio en REPARTO y RETIRO. En CONTEO, null es la bodega de faena. En
    # DESPACHO siempre null: la planta despacha a la faena, no a un campamento.
    camp = models.ForeignKey(
        'camps.Camp', on_delete=models.PROTECT, null=True, blank=True, related_name='linen_movements'
    )
    # Solo el DESPACHO lleva número (`HD-2026-0001`): es el que va impreso en la
    # guía que viaja con la carga.
    number = models.CharField('N° de despacho', max_length=20, blank=True)

    # Momento real del movimiento. En la app móvil es el del teléfono, no el de
    # la sincronización: un retiro registrado sin señal a las 10:00 ocurrió a
    # las 10:00 aunque llegue al servidor a las 18:00.
    occurred_at = models.DateTimeField('Momento del movimiento', db_index=True)
    registered_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name='+'
    )
    note = models.TextField(blank=True)

    # Evidencia de terreno, como en las entregas: la app móvil la exige.
    latitude = models.DecimalField(max_digits=9, decimal_places=6, null=True, blank=True)
    longitude = models.DecimalField(max_digits=9, decimal_places=6, null=True, blank=True)
    accuracy_meters = models.FloatField(null=True, blank=True)

    voided_at = models.DateTimeField(null=True, blank=True)
    voided_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name='+'
    )
    void_reason = models.CharField(max_length=200, blank=True)

    class Meta:
        verbose_name = 'Movimiento de lencería'
        verbose_name_plural = 'Movimientos de lencería'
        ordering = ['-occurred_at', '-id']
        indexes = [
            models.Index(fields=['company', 'occurred_at'], name='linen_company_at_idx'),
            models.Index(fields=['camp', 'occurred_at'], name='linen_camp_at_idx'),
        ]
        constraints = [
            models.UniqueConstraint(
                fields=['number'], condition=~models.Q(number=''), name='unique_linen_dispatch_number'
            ),
            models.CheckConstraint(
                check=~models.Q(kind__in=['REPARTO', 'RETIRO']) | models.Q(camp__isnull=False),
                name='linen_field_movement_has_camp',
            ),
            models.CheckConstraint(
                check=~models.Q(kind='DESPACHO') | models.Q(camp__isnull=True),
                name='linen_dispatch_has_no_camp',
            ),
        ]

    def __str__(self) -> str:
        return f'{self.get_kind_display()} {self.number or self.pk} - {self.company.name}'

    @property
    def is_voided(self) -> bool:
        return self.voided_at is not None


class LinenMovementLine(models.Model):
    """Cuántas piezas de un tipo de lencería mueve (o cuenta) el movimiento."""

    movement = models.ForeignKey(LinenMovement, on_delete=models.CASCADE, related_name='lines')
    garment_type = models.ForeignKey(GarmentType, on_delete=models.PROTECT, related_name='+')
    # En los flujos, las piezas que se movieron. En el CONTEO, las que se
    # contaron físicamente en ese lugar.
    quantity = models.PositiveIntegerField('Cantidad')
    # Solo CONTEO: contado menos el saldo que el sistema tenía justo antes. Es
    # informativo —el saldo se recalcula desde lo contado, no sumando esto— y
    # sirve para ver de cuánto fue el ajuste.
    difference = models.IntegerField('Diferencia del conteo', null=True, blank=True)

    class Meta:
        verbose_name = 'Línea de movimiento'
        verbose_name_plural = 'Líneas de movimiento'
        constraints = [
            models.UniqueConstraint(fields=['movement', 'garment_type'], name='unique_garment_type_per_movement'),
        ]

    def __str__(self) -> str:
        return f'{self.quantity} x {self.garment_type.name}'


class DispatchCounter(models.Model):
    """Correlativo anual de los despachos (`HD-2026-0001`).

    Se bloquea al emitir un número. El número de lote anterior se calculaba
    leyendo el último y sumando uno, y dos despachos simultáneos podían
    quedarse con el mismo.
    """

    year = models.PositiveIntegerField(unique=True)
    last_number = models.PositiveIntegerField(default=0)

    class Meta:
        verbose_name = 'Correlativo de despacho'
        verbose_name_plural = 'Correlativos de despacho'

    def __str__(self) -> str:
        return f'{self.year}: {self.last_number}'
