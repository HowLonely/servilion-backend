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
docker compose -f docker-compose.prod.yml --env-file .env.prod up -d api celery_worker

# nginx doesn't get recreated when only api's image changes, so it keeps
# proxying to the old container's dead IP until restarted.
docker compose -f docker-compose.prod.yml --env-file .env.prod restart nginx

docker image prune -f
