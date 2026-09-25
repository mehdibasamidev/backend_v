#!/usr/bin/env bash
#
# Shared helpers for scripts/backup.sh and scripts/restore.sh.
# Sourced, never executed. The scripts set -euo pipefail before sourcing it.
# shellcheck disable=SC2034  # the variables below are used by the sourcing scripts

# Container names are fixed by `container_name:` in docker-compose.yml, so the
# scripts address them with plain `docker` commands and only fall back to
# `docker compose` where a container or volume has to be (re)created.
DB_CONTAINER=spacedigital_vpn_db
MINIO_CONTAINER=spacedigital_vpn_minio
REDIS_CONTAINER=spacedigital_vpn_redis
DJANGO_CONTAINER=spacedigital_vpn_django
BOT_CONTAINER=spacedigital_vpn_telegram_bot

# Compose service names (the same strings in this project).
DB_SERVICE=$DB_CONTAINER
MINIO_SERVICE=$MINIO_CONTAINER

# Where the MinIO container keeps its data (compose: `server /data`).
MINIO_DATA_DIR=/data
# `docker cp $MINIO_CONTAINER:/data -` streams a tar rooted at the directory's
# basename, so every entry of minio-data.tar.gz starts with this prefix.
MINIO_ARCHIVE_PREFIX=data/

# Row counts recorded at backup time and compared after the restore. Django's
# default naming: <app_label>_<model>. A table the source does not have yet
# (older code) is recorded as "missing" and skipped by the comparison.
COUNT_TABLES=(
  django_migrations
  account_user
  vpn_uservpnsubscription
  vpn_paymentproof
  bot_telegramprofile
  referral_referralcode
)

log()  { printf '\033[1;34m==>\033[0m %s\n' "$*"; }
info() { printf '    %s\n' "$*"; }
warn() { printf '\033[1;33mWARNING:\033[0m %s\n' "$*" >&2; }
die()  { printf '\033[1;31mERROR:\033[0m %s\n' "$*" >&2; exit 1; }

require_cmd() {
  command -v "$1" >/dev/null 2>&1 || die "'$1' is required but is not installed"
}

# Sets COMPOSE to the compose command this host has.
detect_compose() {
  if docker compose version >/dev/null 2>&1; then
    COMPOSE=(docker compose)
  elif command -v docker-compose >/dev/null 2>&1; then
    COMPOSE=(docker-compose)
  else
    die "neither 'docker compose' nor 'docker-compose' is available"
  fi
}

container_exists()  { docker inspect "$1" >/dev/null 2>&1; }
container_running() { [ "$(docker inspect -f '{{.State.Running}}' "$1" 2>/dev/null)" = "true" ]; }

# env_get KEY [DEFAULT]: one value from $ENV_FILE, read the way python-decouple
# reads it (last assignment wins, one pair of surrounding quotes stripped, no
# inline comments). Sourcing .env in the shell is not an option: an unquoted
# value with a space or a '$' in it would break or expand.
env_get() {
  local key=$1 default=${2-} line value
  line=$(grep -E "^[[:space:]]*(export[[:space:]]+)?${key}[[:space:]]*=" "$ENV_FILE" | tail -n 1 || true)
  if [ -z "$line" ]; then
    printf '%s' "$default"
    return 0
  fi
  value=${line#*=}
  value=${value#"${value%%[![:space:]]*}"}   # ltrim
  value=${value%"${value##*[![:space:]]}"}   # rtrim
  case $value in
    \"*\") value=${value#\"}; value=${value%\"} ;;
    \'*\') value=${value#\'}; value=${value%\'} ;;
  esac
  printf '%s' "$value"
}

# Reads the few settings the scripts need from $PROJECT_DIR/.env.
load_env() {
  ENV_FILE=$PROJECT_DIR/.env
  [ -f "$ENV_FILE" ] || die "$ENV_FILE not found; POSTGRES_* and MINIO_* are read from it"
  POSTGRES_DB=$(env_get POSTGRES_DB)
  POSTGRES_USER=$(env_get POSTGRES_USER)
  if [ -z "$POSTGRES_DB" ] || [ -z "$POSTGRES_USER" ]; then
    die "POSTGRES_DB and POSTGRES_USER must be set in $ENV_FILE"
  fi
  MINIO_BUCKET_PUBLIC=$(env_get MINIO_BUCKET_NAME media)
  MINIO_BUCKET_PRIVATE=$(env_get MINIO_PRIVATE_BUCKET_NAME private)
}

# ---------------------------------------------------------------- PostgreSQL

# pg_sql DBNAME SQL: psql inside the db container over its unix socket, which
# the official postgres image trusts (no password needed). Tuples only.
pg_sql() {
  docker exec -i "$DB_CONTAINER" psql -X -q -tA -v ON_ERROR_STOP=1 \
    -U "$POSTGRES_USER" -d "$1" -c "$2"
}

pg_database_exists() {
  [ "$(pg_sql postgres "SELECT 1 FROM pg_database WHERE datname = '$POSTGRES_DB'")" = "1" ]
}

pg_database_list() {
  pg_sql postgres "SELECT string_agg(datname, ', ' ORDER BY datname) FROM pg_database WHERE NOT datistemplate"
}

pg_public_table_count() {
  pg_sql "$POSTGRES_DB" "SELECT count(*) FROM pg_tables WHERE schemaname = 'public'"
}

# pg_table_count TABLE -> a number, or "missing" when the table does not exist.
pg_table_count() {
  pg_sql "$POSTGRES_DB" "SELECT count(*) FROM \"$1\"" 2>/dev/null || echo missing
}

# Waits for the real server. The image's first boot runs a temporary postgres
# that listens on the unix socket only, so readiness is probed over TCP.
wait_for_postgres() {

  for _ in $(seq 1 90); do
    if docker exec "$DB_CONTAINER" pg_isready -h 127.0.0.1 -U "$POSTGRES_USER" -d postgres >/dev/null 2>&1; then
      return 0
    fi
    sleep 1
  done
  die "PostgreSQL in $DB_CONTAINER did not become ready in 90s (docker logs $DB_CONTAINER)"
}

# -------------------------------------------------------------------- images

# image_ref CONTAINER -> "tags digests" of the image the container runs.
image_ref() {
  local id
  id=$(docker inspect -f '{{.Image}}' "$1" 2>/dev/null) || { echo container-missing; return 0; }
  docker image inspect -f '{{join .RepoTags ","}} {{join .RepoDigests ","}}' "$id" 2>/dev/null || echo "$id"
}

# image_digest CONTAINER -> sha256:... of the image, empty when unknown. The
# digest is content-addressed, so it compares across registries and mirrors.
image_digest() {
  local id
  id=$(docker inspect -f '{{.Image}}' "$1" 2>/dev/null) || return 0
  docker image inspect -f '{{join .RepoDigests ","}}' "$id" 2>/dev/null \
    | grep -o 'sha256:[0-9a-f]*' | head -n 1 || true
}

# --------------------------------------------------------------------- MinIO

# Helpers over a `tar t` listing of the MinIO data dir. In MinIO's single-drive
# layout every object is a directory holding one xl.meta, so counting xl.meta
# files under <bucket>/ counts objects. .minio.sys/ is MinIO's own metadata
# (the bucket policies live there) and is skipped.
listing_strip() { sed "s#^${MINIO_ARCHIVE_PREFIX}##" "$1"; }

# listing_buckets LISTING -> bucket names, one per line
listing_buckets() {
  listing_strip "$1" | awk -F/ 'NF > 1 && $1 != "" && $1 != ".minio.sys" { print $1 }' | sort -u
}

# listing_object_count LISTING [BUCKET] -> number of objects (in BUCKET)
listing_object_count() {
  listing_strip "$1" | awk -v b="${2-}" -F/ '
    /\/xl\.meta$/ && $1 != ".minio.sys" && (b == "" || $1 == b) { n++ }
    END { print n + 0 }'
}

# listing_sample_key LISTING BUCKET -> one object key of BUCKET (empty if none)
listing_sample_key() {
  listing_strip "$1" | awk -v b="$2" -F/ '
    /\/xl\.meta$/ && $1 == b {
      sub("^" b "/", ""); sub("/xl\\.meta$", ""); print; exit
    }'
}

# minio_listing_to FILE: `tar t` listing of the container's live data dir.
# Works whether the container is running or stopped.
minio_listing_to() {
  docker cp "$MINIO_CONTAINER:$MINIO_DATA_DIR" - | tar t > "$1"
}
