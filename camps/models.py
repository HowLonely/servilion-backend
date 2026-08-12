import uuid

from django.db import models

from common.models import TimeStampedModel


class Faena(TimeStampedModel):
    """Sitio físico donde se aloja la gente (ej. Peñón). Dueño de los campamentos.

    Es deliberadamente independiente del cliente. Una puerta existe una sola vez
    y lleva un solo QR pegado; en ella puede alojarse el trabajador de cualquier
    contratista, se le facture a quien se le facture.

    Antes los campamentos colgaban de `Client`, lo que ataba el lugar físico a
    quién paga. Con un solo cliente parecía correcto, pero obligaba a duplicar
    campamentos y a emitir un segundo QR para la misma puerta apenas apareciera
    un segundo cliente en la misma faena. Eso ya había ocurrido por la carga de
    datos: 373 campamentos registrados para 139 reales, y 641 puertas con más de
    un QR.
    """

    name = models.CharField('Nombre', max_length=100, unique=True)
    is_active = models.BooleanField(default=True)

    class Meta:
        verbose_name = 'Faena'
        verbose_name_plural = 'Faenas'
        ordering = ['name']

    def __str__(self) -> str:
        return self.name


class Camp(TimeStampedModel):
    """Campamento/patio de una faena donde se alojan los trabajadores.

    Cuelga de `Faena` —lo físico— y no de `Client` ni de `Company`: varias
    empresas contratistas, incluso de clientes distintos, alojan a su gente en
    el mismo campamento, y la ropa se reparte por habitación, no por empresa.

    Reemplaza al antiguo `Worker.camp` (texto libre), que no permitía asociar
    una habitación a una entrega ni detectar dos formas de escribir el mismo
    campamento.
    """

    faena = models.ForeignKey(Faena, on_delete=models.PROTECT, related_name='camps')
    name = models.CharField('Nombre', max_length=100)
    is_active = models.BooleanField(default=True)

    class Meta:
        verbose_name = 'Campamento'
        verbose_name_plural = 'Campamentos'
        ordering = ['faena', 'name']
        constraints = [
            models.UniqueConstraint(fields=['faena', 'name'], name='unique_camp_per_faena'),
        ]
        indexes = [models.Index(fields=['faena', 'is_active'], name='camp_faena_active_idx')]

    def __str__(self) -> str:
        return self.name


class Room(TimeStampedModel):
    """Habitación/pieza de un campamento. Es el destino físico de la entrega.

    `qr_code` es el código pegado en la puerta: la app móvil lo escanea al
    entregar el morral y el backend lo resuelve a esta fila. Es un UUID y no el
    número de pieza porque el número se repite entre campamentos y puede
    reasignarse; el QR identifica la puerta de forma única y estable.
    """

    camp = models.ForeignKey(Camp, on_delete=models.CASCADE, related_name='rooms')
    number = models.CharField('Número', max_length=20)
    qr_code = models.UUIDField('Código QR de la puerta', default=uuid.uuid4, editable=False, unique=True)
    is_active = models.BooleanField(default=True)

    class Meta:
        verbose_name = 'Habitación'
        verbose_name_plural = 'Habitaciones'
        ordering = ['camp', 'number']
        constraints = [
            models.UniqueConstraint(fields=['camp', 'number'], name='unique_room_per_camp'),
        ]
        indexes = [models.Index(fields=['camp', 'is_active'], name='room_camp_active_idx')]

    def __str__(self) -> str:
        return f'{self.camp.name} · {self.number}'
