from django.db import models

from common.models import TimeStampedModel
from companies.models import Company


class Worker(TimeStampedModel):
    """Trabajador de una empresa cliente: es quien entrega/recibe ropa en la lavandería.

    Es un cliente final del servicio, no un usuario del sistema (ver
    `authentication.User` para el staff que opera el panel/app).
    """

    company = models.ForeignKey(Company, on_delete=models.PROTECT, related_name='workers')
    badge_code = models.CharField('Código/credencial', max_length=20)
    full_name = models.CharField(max_length=100)
    national_id = models.CharField('RUT', max_length=15, db_index=True, blank=True)
    camp = models.CharField('Campamento/patio', max_length=30, blank=True)
    room = models.CharField('Pieza', max_length=20, blank=True)
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
