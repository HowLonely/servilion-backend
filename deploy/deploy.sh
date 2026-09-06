#!/bin/bash
set -euo pipefail

cd /home/ubuntu/servilion/backend

sudo -u ubuntu git fetch origin main
sudo -u ubuntu git reset --hard origin/main

docker compose -f docker-compose.prod.yml --env-file .env.prod up -d --build
docker image prune -f
