from django.contrib.auth.models import AbstractUser
from django.db import models

from common.models import TimeStampedModel


class StaffRole(TimeStampedModel):
    """Rol del staff: un nombre y el conjunto de permisos que habilita.

    Antes los roles eran cinco constantes en el código y lo que cada uno podía
    hacer estaba repartido en los decoradores de cada endpoint. Ahora son filas
    editables desde el módulo de configuración (panel web y terminal): un
    administrador puede crear un rol nuevo —ej. "Bodega", solo despacho de
    hotelería— y decidir qué permisos lleva, sin tocar código.

    `permissions` guarda códigos del catálogo `authentication.permissions.PERMISSIONS`.
    Es una lista y no una tabla intermedia porque el catálogo vive en el código
    (cada permiso lo exige un endpoint concreto) y la lista viaja entera en la
    sincronización con el servidor local.

    El rol ADMIN es especial: tiene siempre todos los permisos, incluidos los que
    se agreguen al catálogo en el futuro, y no se puede editar ni borrar. Es la
    garantía de que nadie queda afuera del sistema por un rol mal configurado.
    """

    code = models.CharField('Código', max_length=40, unique=True)
    name = models.CharField('Nombre', max_length=60)
    description = models.CharField('Descripción', max_length=200, blank=True)
    permissions = models.JSONField('Permisos', default=list, blank=True)
    # Los cinco roles de siempre. Se pueden editar (salvo ADMIN) pero no
    # borrar: el código y la documentación los nombran.
    is_system = models.BooleanField(default=False)
    is_active = models.BooleanField(default=True)

    class Meta:
        verbose_name = 'Rol'
        verbose_name_plural = 'Roles'
        ordering = ['name']

    def __str__(self) -> str:
        return self.name


class User(AbstractUser):
    """Usuario de staff (panel web, terminal de planta y app móvil de operadores).

    No confundir con `workers.Worker`: ese modelo representa a las personas
    a las que se les lava ropa (clientes finales de las empresas contratantes),
    no a quienes operan el sistema.
    """

    class Role(models.TextChoices):
        """Códigos de los roles de sistema (ver `StaffRole.is_system`).

        Se conservan como constantes porque el código los sigue nombrando
        (siembras, pruebas, el rol ADMIN que atraviesa todo). Ya no restringen
        el campo `role`: un administrador puede crear roles nuevos desde el
        módulo de configuración, y lo que cada rol puede hacer vive en
        `StaffRole.permissions`, no aquí.
        """

        ADMIN = 'ADMIN', 'Administrador'
        SUPERVISOR = 'SUPERVISOR', 'Supervisor'
        PESAJE = 'PESAJE', 'Pesaje'
        DIGITADOR_OT = 'DIGITADOR_OT', 'Digitador de OT'
        DIGITADOR_EMPAQUE = 'DIGITADOR_EMPAQUE', 'Digitador de Empaque'

    # Código de `StaffRole`. Es texto y no FK para que los clientes (web,
    # terminal, app móvil) sigan leyendo `user.role` igual que siempre.
    role = models.CharField(max_length=40, default=Role.DIGITADOR_OT)
    phone = models.CharField(max_length=20, blank=True)
    # Cuenta técnica de un servidor local de planta (ver `sync.Node`): no es una
    # persona, no inicia sesión y no aparece en el módulo de usuarios.
    is_service_account = models.BooleanField(default=False)
    # Marca que usa la sincronización con el servidor local para decidir qué
    # versión de un usuario editado en ambos lados gana (last-write-wins).
    updated_at = models.DateTimeField(auto_now=True, db_index=True)

    def __str__(self) -> str:
        return self.get_full_name() or self.username

    @property
    def role_name(self) -> str:
        role = StaffRole.objects.filter(code=self.role).only('name').first()
        return role.name if role else self.role

    def get_role_display(self) -> str:
        return self.role_name
