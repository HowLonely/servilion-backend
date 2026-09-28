"""Borra del servidor local las guías y pesajes fuera de la ventana (siguen en la nube)."""

from django.core.management.base import BaseCommand, CommandError

from common.node import is_edge
from sync.window import prune_local


class Command(BaseCommand):
    help = 'Aplica la ventana de retención del servidor local.'

    def handle(self, *args, **options):
        if not is_edge():
            raise CommandError('Solo el servidor local recorta su base; la nube guarda todo.')
        self.stdout.write(str(prune_local()))
