"""Registra un servidor local en la nube y muestra su token (se corre en la nube)."""

from django.core.management.base import BaseCommand, CommandError

from common.node import is_edge
from sync.cloud import register_node


class Command(BaseCommand):
    help = 'Da de alta (o renueva el token de) un servidor local de planta. Correr en la nube.'

    def add_arguments(self, parser):
        parser.add_argument('name', help='Nombre del servidor local, ej. antofagasta')

    def handle(self, *args, **options):
        if is_edge():
            raise CommandError('Este comando se corre en la nube, no en el servidor local.')
        node, token = register_node(options['name'])
        self.stdout.write(self.style.SUCCESS(f'Servidor local "{node.name}" registrado.'))
        self.stdout.write('Copia este token en SYNC_NODE_TOKEN del servidor local (no se vuelve a mostrar):')
        self.stdout.write(token)
