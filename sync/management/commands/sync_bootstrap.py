"""Instala el servidor local: baja de la nube el catálogo completo y la operación reciente."""

from django.core.management.base import BaseCommand, CommandError

from common.node import is_edge
from sync import client, edge


class Command(BaseCommand):
    help = 'Baja la foto inicial desde la nube. Correr una vez en el servidor local.'

    def add_arguments(self, parser):
        parser.add_argument(
            '--force',
            action='store_true',
            help='Reemplaza los datos locales aunque haya guías (solo si ya están en la nube).',
        )
        parser.add_argument(
            '--refresh',
            action='store_true',
            help='No vacía las tablas: solo actualiza con lo que tiene la nube.',
        )

    def handle(self, *args, **options):
        if not is_edge():
            raise CommandError('Este comando se corre en el servidor local (SERVILION_NODE=edge).')
        if not client.is_configured():
            raise CommandError('Falta SYNC_CLOUD_URL o SYNC_NODE_TOKEN en el .env del servidor local.')
        try:
            counts = edge.bootstrap(wipe=not options['refresh'], force=options['force'])
        except edge.BootstrapRefused as exc:
            raise CommandError(str(exc)) from exc
        except (client.CloudUnavailable, client.CloudRejected) as exc:
            raise CommandError(f'No se pudo bajar la foto desde la nube: {exc}') from exc
        for table, total in counts.items():
            self.stdout.write(f'  {table:<34} {total:>8}')
        self.stdout.write(self.style.SUCCESS('Servidor local listo.'))
