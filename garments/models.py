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
    # Qué tipos se ofrecen en hotelería (despacho, reparto, retiro y conteo de
    # lencería). No excluye al tipo de las guías de trabajadores: una toalla
    # puede venir en un morral y también circular como lencería del campamento.
    # Sin esta marca, el stock rotativo aceptaría cualquier prenda del catálogo
    # y el saldo por campamento se llenaría de tipos que nunca circulan ahí.
    is_linen = models.BooleanField('Se usa en hotelería', default=False)

    class Meta:
        verbose_name = 'Tipo de prenda'
        verbose_name_plural = 'Tipos de prenda'
        ordering = ['name']

    def __str__(self) -> str:
        return f'{self.name} ({self.code})'
