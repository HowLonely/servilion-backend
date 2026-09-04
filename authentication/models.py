from django.contrib.auth.models import AbstractUser
from django.db import models


class User(AbstractUser):
    """Usuario de staff (panel web y app móvil de operadores).

    No confundir con `workers.Worker`: ese modelo representa a las personas
    a las que se les lava ropa (clientes finales de las empresas contratantes),
    no a quienes operan el sistema.
    """

    class Role(models.TextChoices):
        """Roles operativos del staff.

        Son cinco y describen puestos reales, no áreas: quien administra el
        sistema, quien supervisa la operación y las tres estaciones de
        Antofagasta, en el orden en que toca el morral: la báscula lo pesa y lo
        etiqueta al llegar sucio, la digitalización pasa la OT física al
        sistema, y el empaque valida el morral limpio antes de despacharlo.

        PESAJE es un puesto propio y no una variante de DIGITADOR_OT porque es
        físicamente otra estación —una balanza y una impresora de etiquetas, en
        la recepción— y la opera quien recibe el camión, no quien tipea la OT.

        La diferencia entre ADMIN y SUPERVISOR es la administración del
        catálogo y el dinero: clientes, empresas, trabajadores, prendas,
        facturación y conflictos de sincronización son solo de ADMIN. Todo lo
        operativo lo comparten.
        """

        ADMIN = 'ADMIN', 'Administrador'
        SUPERVISOR = 'SUPERVISOR', 'Supervisor'
        PESAJE = 'PESAJE', 'Pesaje'
        DIGITADOR_OT = 'DIGITADOR_OT', 'Digitador de OT'
        DIGITADOR_EMPAQUE = 'DIGITADOR_EMPAQUE', 'Digitador de Empaque'

    role = models.CharField(max_length=20, choices=Role.choices, default=Role.DIGITADOR_OT)
    phone = models.CharField(max_length=20, blank=True)

    def __str__(self) -> str:
        return self.get_full_name() or self.username
