#!/bin/bash
set -euo pipefail

cd /home/ubuntu/servilion/backend

sudo -u ubuntu git fetch origin main
sudo -u ubuntu git reset --hard origin/main

docker compose -f docker-compose.prod.yml --env-file .env.prod up -d --build

# nginx doesn't get recreated when only api's image changes, so it keeps
# proxying to the old container's dead IP until restarted.
docker compose -f docker-compose.prod.yml --env-file .env.prod restart nginx

docker image prune -f
