#!/bin/bash
set -euo pipefail

cd /home/ubuntu/servilion/backend

sudo -u ubuntu git fetch origin main
sudo -u ubuntu git reset --hard origin/main

# Images are built and pushed to GHCR by CI (see .github/workflows/deploy.yml);
# this box only ever pulls, never builds. Keeps a t3.micro from OOM-killing
# other containers mid-build (see incident 2026-09-08).
docker compose -f docker-compose.prod.yml --env-file .env.prod pull
docker compose -f docker-compose.prod.yml --env-file .env.prod up -d

# nginx doesn't get recreated when only api's image changes, so it keeps
# proxying to the old container's dead IP until restarted.
docker compose -f docker-compose.prod.yml --env-file .env.prod restart nginx

docker image prune -f
