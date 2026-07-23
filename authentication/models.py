from django.contrib.auth.models import AbstractUser
from django.db import models


class User(AbstractUser):
    """Usuario de staff (panel web y app móvil de operadores).

    No confundir con `workers.Worker`: ese modelo representa a las personas
    a las que se les lava ropa (clientes finales de las empresas contratantes),
    no a quienes operan el sistema.
    """

    class Role(models.TextChoices):
        ADMIN = 'ADMIN', 'Administrador'
        RECEPCION = 'RECEPCION', 'Recepción'
        LAVANDERIA = 'LAVANDERIA', 'Lavandería'
        DESPACHO = 'DESPACHO', 'Despacho'
        SUPERVISOR = 'SUPERVISOR', 'Supervisor'

    role = models.CharField(max_length=20, choices=Role.choices, default=Role.RECEPCION)
    phone = models.CharField(max_length=20, blank=True)

    def __str__(self) -> str:
        return self.get_full_name() or self.username
