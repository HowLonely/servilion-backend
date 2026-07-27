from django.db import models

from common.models import TimeStampedModel
from companies.models import Company
from camps.models import Room


class Worker(TimeStampedModel):
    """Trabajador de una empresa cliente: es quien entrega/recibe ropa en la lavandería.

    Es un cliente final del servicio, no un usuario del sistema (ver
    `authentication.User` para el staff que opera el panel/app).
    """

    company = models.ForeignKey(Company, on_delete=models.PROTECT, related_name='workers')
    badge_code = models.CharField('Código/credencial', max_length=20)
    full_name = models.CharField(max_length=100)
    national_id = models.CharField('RUT', max_length=15, db_index=True, blank=True)
    # Dónde vive HOY. Los antiguos `camp`/`room` (texto libre) se normalizaron a
    # `camps.Room` para poder escanear el QR de la puerta al entregar.
    # SET_NULL y no PROTECT: dar de baja una habitación no debe bloquearse por
    # los trabajadores que la ocuparon; simplemente quedan sin asignar.
    # Ojo: este campo cambia si el trabajador se muda. El destino de una entrega
    # ya digitalizada vive en `orders.LaundryOrder.habitacion_entrega`.
    current_room = models.ForeignKey(
        Room, on_delete=models.SET_NULL, null=True, blank=True, related_name='occupants'
    )
    shift = models.CharField('Turno', max_length=10, blank=True)
    position = models.CharField('Cargo', max_length=50, blank=True)
    area = models.CharField(max_length=50, blank=True)
    phone = models.CharField(max_length=20, blank=True)
    is_active = models.BooleanField(default=True)

    class Meta:
        verbose_name = 'Trabajador'
        verbose_name_plural = 'Trabajadores'
        ordering = ['full_name']
        indexes = [models.Index(fields=['company', 'is_active'])]
        constraints = [
            models.UniqueConstraint(fields=['company', 'badge_code'], name='unique_badge_code_per_company'),
        ]

    def __str__(self) -> str:
        return f'{self.full_name} ({self.badge_code})'
