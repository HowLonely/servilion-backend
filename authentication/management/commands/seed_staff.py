"""Siembra una cuenta de staff por cada rol operativo.

Existe para poder recorrer el flujo completo (FLUJO_NEGOCIO.md §4) sin inventar
usuarios a mano cada vez que se levanta una base limpia: pesar el morral,
digitalizar la OT, empacarlo, confirmar la recepción en faena y administrar el
catálogo son cinco roles distintos, y con una sola cuenta ADMIN no se ve nunca lo que ve el
operador real (ADMIN atraviesa toda restricción, ver
`authentication/permissions.py`).

Las cuentas llevan prefijo `demo_` a propósito: son de desarrollo, se documentan
con su contraseña en USUARIOS.md y el comando las repone tal cual cada vez que
corre. Nunca ejecutar esto contra producción.
"""

from django.core.management.base import BaseCommand
from django.db import transaction

from authentication.models import User

# Contraseña única para las cinco cuentas: son de desarrollo y se tipean a
# diario en dos clientes distintos (panel web y terminal de escritorio).
DEFAULT_PASSWORD = 'Servilion.2026'

# Un usuario por rol. El nombre visible dice "Demo" para que en la cabecera del
# panel se distinga de una cuenta real de un operador.
SEED_USERS = (
    ('demo_admin', User.Role.ADMIN, 'Demo', 'Administrador'),
    ('demo_supervisor', User.Role.SUPERVISOR, 'Demo', 'Supervisor'),
    ('demo_pesaje', User.Role.PESAJE, 'Demo', 'Pesaje'),
    ('demo_ot', User.Role.DIGITADOR_OT, 'Demo', 'Digitador OT'),
    ('demo_empaque', User.Role.DIGITADOR_EMPAQUE, 'Demo', 'Digitador Empaque'),
)


class Command(BaseCommand):
    help = 'Crea o repone una cuenta de staff por cada rol (solo desarrollo).'

    def add_arguments(self, parser):
        parser.add_argument(
            '--password',
            default=DEFAULT_PASSWORD,
            help=f'Contraseña para las cinco cuentas (por defecto "{DEFAULT_PASSWORD}").',
        )

    @transaction.atomic
    def handle(self, *args, **options):
        password = options['password']
        for username, role, first_name, last_name in SEED_USERS:
            user, created = User.objects.get_or_create(username=username)
            user.role = role
            user.first_name = first_name
            user.last_name = last_name
            user.is_active = True
            # Las cuentas demo no entran al admin de Django: su alcance es la
            # API (panel web y terminal). El superusuario sigue siendo aparte.
            user.is_staff = False
            user.is_superuser = False
            # Se repone siempre: la contraseña está documentada en USUARIOS.md y
            # el comando es la fuente de verdad de esa documentación.
            user.set_password(password)
            user.save()
            estado = 'creado' if created else 'actualizado'
            self.stdout.write(f'  {username:<16} {role:<18} {estado}')

        self.stdout.write(
            self.style.SUCCESS(f'{len(SEED_USERS)} cuentas de staff listas (contraseña: {password}).')
        )
