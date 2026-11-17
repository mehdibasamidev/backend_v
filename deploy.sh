#!/bin/sh
set -eu

cd "$(dirname "$0")"

if [ "$(git branch --show-current)" != main ]; then
    echo "Deploy from a checkout on the main branch." >&2
    exit 1
fi

git pull --ff-only origin main
docker compose up -d --build --wait --wait-timeout 350 \
    spacedigital_vpn_django spacedigital_vpn_telegram_bot
docker compose ps spacedigital_vpn_django spacedigital_vpn_telegram_bot
