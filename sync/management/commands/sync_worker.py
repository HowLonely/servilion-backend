"""Proceso permanente del servidor local: mantiene la base sincronizada con la nube.

- **Envío casi inmediato.** Cada escritura sincronizada dispara un NOTIFY desde
  PostgreSQL al confirmarse; este proceso lo escucha y envía el cambio a la
  nube en uno o dos segundos. Si no hay internet, el cambio espera en el
  outbox —en disco, dentro de la base— y sale apenas vuelve la conexión.
- **Recepción por sondeo** cada `SYNC_PULL_INTERVAL_SECONDS` (web y app móvil).
- **Limpieza diaria** de lo que salió de la ventana de 90 días.

Nada de esto frena a la planta: las terminales escriben siempre en la base
local, y una caída de internet solo retrasa la llegada a la nube.
"""

import logging
import select
import time
from datetime import timedelta

import psycopg2
from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.db import close_old_connections, connection
from django.utils import timezone
from django.utils.dateparse import parse_datetime

from common.node import is_edge
from sync import client, edge, state, triggers
from sync.window import prune_local

logger = logging.getLogger('sync.worker')

PRUNE_EVERY = timedelta(hours=24)
MAX_BACKOFF_SECONDS = 60
# Tras un NOTIFY se espera un instante: digitalizar una guía son varias filas
# (guía, prendas, pesaje) y conviene que viajen en el mismo lote.
DEBOUNCE_SECONDS = 0.5


def _listen_connection():
    params = connection.get_connection_params()
    listener = psycopg2.connect(**params)
    listener.set_isolation_level(psycopg2.extensions.ISOLATION_LEVEL_AUTOCOMMIT)
    with listener.cursor() as cursor:
        cursor.execute(f'LISTEN {triggers.NOTIFY_CHANNEL};')
    return listener


def _wait(listener, timeout: float) -> bool:
    """Espera un NOTIFY hasta `timeout` segundos. True si llegó alguno."""
    if listener is None:
        time.sleep(timeout)
        return False
    ready, _, _ = select.select([listener], [], [], timeout)
    if not ready:
        return False
    listener.poll()
    got = bool(listener.notifies)
    listener.notifies.clear()
    return got


def _prune_due() -> bool:
    last = state.get(state.LAST_PRUNE_AT)
    last_at = parse_datetime(last) if last else None
    return last_at is None or timezone.now() - last_at >= PRUNE_EVERY


class Command(BaseCommand):
    help = 'Sincroniza en forma continua el servidor local con la nube.'

    def add_arguments(self, parser):
        parser.add_argument('--once', action='store_true', help='Un solo ciclo (para diagnóstico).')

    def handle(self, *args, **options):
        if not is_edge():
            raise CommandError('El proceso de sincronización corre solo en el servidor local.')
        if not client.is_configured():
            raise CommandError('Falta SYNC_CLOUD_URL o SYNC_NODE_TOKEN en el .env del servidor local.')

        edge.ensure_sequences()
        listener = None
        backoff = 1
        next_pull = 0.0
        self.stdout.write(f'Sincronizando con {settings.SYNC_CLOUD_URL}')

        while True:
            close_old_connections()
            if listener is None:
                try:
                    listener = _listen_connection()
                except psycopg2.Error:
                    logger.exception('No se pudo escuchar NOTIFY; se sigue por sondeo.')

            try:
                if not edge.is_bootstrapped():
                    self.stdout.write('Primera sincronización: bajando la foto completa de la nube...')
                    edge.bootstrap(wipe=not edge.has_local_operation())
                edge.push_all()
                if time.monotonic() >= next_pull:
                    edge.pull_all()
                    next_pull = time.monotonic() + settings.SYNC_PULL_INTERVAL_SECONDS
                if _prune_due():
                    logger.info('Limpieza de la ventana local: %s', prune_local())
                    state.put(state.LAST_PRUNE_AT, timezone.now().isoformat())
                state.put(state.ONLINE, True)
                state.put(state.LAST_ERROR, None)
                backoff = 1
            except client.CloudUnavailable as exc:
                state.put(state.ONLINE, False)
                state.put(state.LAST_ERROR, f'Sin conexión con la nube: {exc}')
                backoff = min(backoff * 2, MAX_BACKOFF_SECONDS)
            except client.CloudRejected as exc:
                state.put(state.ONLINE, True)
                state.put(state.LAST_ERROR, f'La nube rechazó la sincronización: {exc.detail}')
                logger.error('La nube rechazó la sincronización: %s', exc)
                backoff = MAX_BACKOFF_SECONDS
            except Exception as exc:  # noqa: BLE001 — el proceso no debe morir por un error puntual
                logger.exception('Error de sincronización')
                state.put(state.LAST_ERROR, f'Error interno: {exc}')
                backoff = min(backoff * 2, MAX_BACKOFF_SECONDS)

            if options['once']:
                return

            online = state.get(state.ONLINE, False)
            timeout = settings.SYNC_PULL_INTERVAL_SECONDS if online and backoff == 1 else backoff
            try:
                if _wait(listener, timeout):
                    time.sleep(DEBOUNCE_SECONDS)
            except (psycopg2.Error, OSError, ValueError):
                logger.warning('Se perdió la escucha de NOTIFY; se reconecta.')
                listener = None
