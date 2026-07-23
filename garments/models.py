from django.db import models

from common.models import TimeStampedModel


class GarmentType(TimeStampedModel):
    """Catálogo de tipos de prenda que la lavandería procesa (ej. PANTALON SLACK, TOALLA).

    El precio ya no vive aquí: se define por cliente en
    `companies.ClientGarmentPrice`. Este catálogo solo tipifica la prenda.
    """

    code = models.CharField(max_length=10, unique=True)
    name = models.CharField(max_length=60)
    is_active = models.BooleanField(default=True)

    class Meta:
        verbose_name = 'Tipo de prenda'
        verbose_name_plural = 'Tipos de prenda'
        ordering = ['name']

    def __str__(self) -> str:
        return f'{self.name} ({self.code})'
