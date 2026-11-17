#!/bin/sh
set -eu

cd "$(dirname "$0")"
exec docker compose exec -T spacedigital_vpn_django \
    python manage.py backup_database_to_telegram "$@"
