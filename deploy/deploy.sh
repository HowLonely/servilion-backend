#!/bin/bash
set -euo pipefail

cd /home/ubuntu/servilion/backend

sudo -u ubuntu git fetch origin main
sudo -u ubuntu git reset --hard origin/main

# Images are built and pushed to GHCR by CI (see .github/workflows/deploy.yml);
# this box only ever pulls, never builds. Keeps a t3.micro from OOM-killing
# other containers mid-build (see incident 2026-09-08).
#
# Scoped to this repo's own services on purpose: an unscoped `pull` also
# tries to fetch web's image, and aborts everything (api and celery_worker
# included) if that tag doesn't exist yet — which happens whenever the two
# repos' independent CI runs finish out of order (see incident 2026-09-09).
docker compose -f docker-compose.prod.yml --env-file .env.prod pull api celery_worker

# Migra con la imagen NUEVA antes de levantarla, mientras el `api` viejo sigue
# sirviendo tráfico con el código viejo. El orden importa: si se migrara
# después de `up -d`, habría una ventana donde el código nuevo ya sirve
# contra el esquema viejo (incidente 2026-09-28: el deploy de cargos express
# se automatizó hasta acá, nadie migró a mano, y `/api/orders/` quedó
# respondiendo 500 porque `service_type` no existía todavía en la base).
# `run --rm` usa la imagen recién bajada sin tocar el contenedor que sigue
# atendiendo requests.
docker compose -f docker-compose.prod.yml --env-file .env.prod run --rm api python manage.py migrate --noinput

docker compose -f docker-compose.prod.yml --env-file .env.prod up -d api celery_worker

# nginx doesn't get recreated when only api's image changes, so it keeps
# proxying to the old container's dead IP until restarted.
docker compose -f docker-compose.prod.yml --env-file .env.prod restart nginx

docker image prune -f
