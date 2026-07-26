import uuid

from django.db import models

from common.models import TimeStampedModel
from companies.models import Client


class Camp(TimeStampedModel):
    """Campamento/patio de una faena donde se alojan los trabajadores.

    Cuelga de `Client` y no de `Company` porque el campamento es la faena
    física: varias empresas contratistas del mismo cliente alojan a su gente en
    el mismo campamento, y la ropa se reparte por habitación, no por empresa.

    Reemplaza al antiguo `Worker.camp` (texto libre), que no permitía asociar
    una habitación a una entrega ni detectar dos formas de escribir el mismo
    campamento.
    """

    client = models.ForeignKey(Client, on_delete=models.CASCADE, related_name='camps')
    name = models.CharField('Nombre', max_length=100)
    is_active = models.BooleanField(default=True)

    class Meta:
        verbose_name = 'Campamento'
        verbose_name_plural = 'Campamentos'
        ordering = ['client', 'name']
        constraints = [
            models.UniqueConstraint(fields=['client', 'name'], name='unique_camp_per_client'),
        ]
        indexes = [models.Index(fields=['client', 'is_active'], name='camp_client_active_idx')]

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
