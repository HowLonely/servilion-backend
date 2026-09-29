# Sincronización nube ⇄ servidor local de planta

El mismo backend corre en dos lugares:

| Nodo | `SERVILION_NODE` | Quién lo usa |
|---|---|---|
| **Nube** (`api.servilion.cl`) | `cloud` | Panel web (consulta y administración) y app móvil de faena |
| **Servidor local** (planta de Antofagasta, repo `servilion-local`) | `edge` | Terminales Servilion Desktop |

La planta trabaja siempre contra su servidor local. Ese servidor le envía a la nube todo lo que pasa en la planta y trae lo que pasa afuera. Un corte de internet solo retrasa la llegada de los cambios; no detiene la planta.

## Quién escribe qué

| Dato | Se escribe en | Política |
|---|---|---|
| Pesaje, digitalización, empaque, despacho, despacho de hotelería | Solo servidor local (`@plant_only`: la nube responde 409) | La planta manda |
| `ref`, `HD-2026-0001`, cupo express | Solo servidor local | Un solo emisor: nunca se repiten |
| Recepción y entrega en faena, reparto y retiro de hotelería | Nube (app móvil) | Se suman a la guía sin pisar lo de planta |
| Usuarios, roles, catálogo, trabajadores | Ambos (web y terminal) | Gana la edición más reciente |
| Conteo y anulación de hotelería | Nube (web) | Gana la edición más reciente |

`ALLOW_PLANT_OPERATIONS=1` habilita las operaciones de planta en un backend `cloud` (desarrollo).

## Cómo funciona

**IDs iguales en los dos lados.** La nube numera desde 1 y el servidor local desde `SYNC_EDGE_ID_START` (10¹²). Una fila conserva su ID al viajar, así la guía 1000000000042 es la misma en la terminal, la web y la app. `edge.ensure_sequences()` ajusta los contadores y es idempotente.

**Changelog por triggers.** Cada tabla de `registry.SYNCED_MODELS` tiene un trigger de PostgreSQL (`sync_track_change`) que anota en `sync_changelog` qué fila cambió. Captura todo, incluidos `bulk_create`, `update()` y SQL crudo. Lo que escribe la propia sincronización no se anota: el aplicador fija la variable de sesión `servilion.sync_applying`, así un cambio recibido no rebota a su origen.

**Envío (local → nube).** El trigger también hace `NOTIFY servilion_sync`. `manage.py sync_worker` lo escucha y envía el lote a `POST /api/sync/push` en uno o dos segundos. El changelog local se borra solo cuando la nube acusa recibo. Sin internet, reintenta con espera creciente.

**Recepción (nube → local).** Cada `SYNC_PULL_INTERVAL_SECONDS`, `GET /api/sync/pull?after=<cursor>`. El cursor es también el acuse: la nube borra lo que todos los servidores ya leyeron. Si un servidor pasa más de 60 días sin leer, la nube responde 410 y el servidor vuelve a bajar la foto completa.

**Aplicación (`apply.py`).** Las reglas son deterministas y simétricas, para que los dos nodos lleguen al mismo resultado sin importar el orden:

- `LWW` (catálogo): gana el `updated_at` mayor; el perdedor queda en `SyncIssue`.
- `ORDER` (guía): base = versión de planta; los hitos vacíos se completan con los del otro lado; el estado queda en el más avanzado (nunca retrocede).
- `MERGE` (pistoleos de faena): un nulo entrante no borra un valor existente.
- `UPSERT`: filas de un solo escritor.
- Claves únicas en choque (el usuario "juan" creado en los dos lados): pierde la fila de ID mayor, a la que se le agrega `~NNNN`.
- Una fila que falla no detiene el lote: queda como `SyncIssue` de tipo ERROR.

**Ventana local (`window.py`).** El servidor local guarda completo el catálogo y la hotelería, pero solo 90 días de guías y pesajes (más las guías que siguen abiertas). `sync_worker` recorta una vez al día, y solo con el outbox vacío. El recorte no viaja a la nube. El listado del histórico con fechas fuera de la ventana, y el detalle de una guía que ya no está, se reenvían a la nube (`proxy.py`) con el token del nodo; sin internet se responde lo local con `X-Servilion-Source: local`.

## Comandos

| Comando | Dónde | Qué hace |
|---|---|---|
| `sync_register_node <nombre>` | Nube | Da de alta un servidor local e imprime su token |
| `sync_bootstrap [--force] [--refresh]` | Local | Baja la foto inicial |
| `sync_worker [--once]` | Local | Proceso permanente de sincronización |
| `sync_prune` | Local | Aplica la ventana de retención a mano |

## Agregar una tabla a la sincronización

1. Agregarla en `registry.SYNCED_MODELS`, en el lugar que le corresponde por dependencias.
2. Crear una migración en `sync` que ejecute `triggers.trigger_sql('<tabla>')` y agregue la tabla a la lista.
3. Si es operativa y debe recortarse, definir su regla en `window.py`.

`sync.tests.TriggerTests.test_registry_matches_triggers` falla si el registro y los triggers no coinciden.
