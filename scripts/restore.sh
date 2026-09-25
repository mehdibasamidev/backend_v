#!/usr/bin/env bash
#
# scripts/restore.sh - load a backups/<timestamp>/ directory written by
# scripts/backup.sh into THIS server's PostgreSQL and MinIO containers.
# Persian walkthrough: docs/MIGRATION_FA.md.
#
# Run it on the NEW server once .env exists there:
#
#   scripts/restore.sh backups/<timestamp>          first restore into an empty stack
#   scripts/restore.sh backups/<timestamp> --force  may also DROP a database that already
#                                                   has tables and wipe a MinIO volume that
#                                                   already holds objects (the second
#                                                   restore, after a rehearsal)
#   scripts/restore.sh ... --yes                    skip the confirmation prompt
#
# In order:
#   1. verifies SHA256SUMS
#   2. starts spacedigital_vpn_db (nothing else) and stops django and the bot
#   3. drops and recreates $POSTGRES_DB, then pg_restore's db.dump into it in
#      one transaction (a failure leaves an empty database, never a half one)
#   4. recreates the MinIO volume from minio-data.tar.gz and starts MinIO
#   5. compares row and object counts with manifest.txt and checks that the
#      public bucket still answers anonymous downloads
#
# It never starts django or the bot; the command to run next is printed.
set -euo pipefail
shopt -s inherit_errexit

SCRIPT_DIR=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
PROJECT_DIR=$(cd "$SCRIPT_DIR/.." && pwd)
# shellcheck source=scripts/_lib.sh
. "$SCRIPT_DIR/_lib.sh"

BACKUP_DIR=""
FORCE=0
YES=0
ALLOW_SAME_HOST=0
while [ $# -gt 0 ]; do
  case $1 in
    --force) FORCE=1 ;;
    --yes|-y) YES=1 ;;
    --allow-same-host) ALLOW_SAME_HOST=1 ;;
    -h|--help) sed -n '3,/^set -euo/p' "$0" | sed '$d' | sed 's/^# \{0,1\}//'; exit 0 ;;
    -*) die "unknown option: $1 (see --help)" ;;
    *) [ -z "$BACKUP_DIR" ] || die "only one backup directory can be given"; BACKUP_DIR=$1 ;;
  esac
  shift
done
[ -n "$BACKUP_DIR" ] || die "usage: scripts/restore.sh backups/<timestamp> [--force] [--yes]"
[ -d "$BACKUP_DIR" ] || die "$BACKUP_DIR is not a directory"
BACKUP_DIR=$(cd "$BACKUP_DIR" && pwd)

require_cmd docker
require_cmd gzip
require_cmd tar
require_cmd sha256sum
detect_compose
cd "$PROJECT_DIR"
load_env

for f in manifest.txt db.dump minio-data.tar.gz minio-listing.txt SHA256SUMS; do
  [ -f "$BACKUP_DIR/$f" ] || die "$BACKUP_DIR/$f is missing; is this a directory written by scripts/backup.sh?"
done

log "Verifying checksums"
(cd "$BACKUP_DIR" && sha256sum --check --quiet SHA256SUMS) \
  || die "checksum mismatch: the backup is incomplete or was damaged in transfer; copy it again"

# mf KEY -> value from manifest.txt (empty when absent)
mf() { grep -m 1 "^$1=" "$BACKUP_DIR/manifest.txt" | cut -d= -f2- || true; }
SRC_HOST=$(mf source_hostname)
SRC_DB=$(mf postgres_db)
SRC_COMMIT=$(mf source_git_commit)
SRC_MODE=$(mf backup_mode)
SRC_MINIO_DIGEST=$(mf image_minio_digest)

if [ "$SRC_HOST" = "$(hostname)" ] && [ $ALLOW_SAME_HOST = 0 ]; then
  die "this backup was taken on this very host ($SRC_HOST). restore.sh is for the NEW server and would wipe the live data here; pass --allow-same-host if that is really what you want."
fi

# --------------------------------------------------------------------- plan

HERE_COMMIT=$(git -C "$PROJECT_DIR" rev-parse HEAD 2>/dev/null || echo unknown)
log "Restore plan"
info "backup:       $BACKUP_DIR"
info "              $SRC_MODE snapshot taken $(mf backup_created_at) on $SRC_HOST"
info "source code:  $SRC_COMMIT (this checkout: $HERE_COMMIT)"
info "database:     '$SRC_DB' on the source -> '$POSTGRES_DB' here (from .env), owner $POSTGRES_USER"
info "minio:        $(mf minio_objects_total) objects in buckets: $(mf minio_buckets)"
if [ "$SRC_COMMIT" != "$HERE_COMMIT" ]; then
  warn "this checkout is not the commit the backup came from. Deploy the same commit first and upgrade afterwards:  git checkout $SRC_COMMIT"
fi
if [ "$SRC_MODE" != "final" ]; then
  warn "this is a LIVE snapshot: fine for a rehearsal, not for the final cutover"
fi
if [ $YES = 0 ]; then
  printf 'This replaces the database and the MinIO data on this server. Type "yes" to continue: '
  read -r answer
  [ "$answer" = "yes" ] || die "aborted"
fi

MISMATCH=0
# compare NAME SOURCE HERE: prints one line, flags a difference
compare() {
  local line
  line=$(printf '%-30s %10s -> %-10s' "$1" "$2" "$3")
  if [ "$2" = "missing" ] || [ -z "$2" ]; then
    info "$line (not on the source, skipped)"
  elif [ "$2" = "$3" ]; then
    info "$line ok"
  else
    warn "$line MISMATCH"
    MISMATCH=1
  fi
}

# ---------------------------------------------------------------- PostgreSQL

log "Starting $DB_SERVICE"
"${COMPOSE[@]}" up -d "$DB_SERVICE"
wait_for_postgres

log "Stopping $DJANGO_CONTAINER and $BOT_CONTAINER (nothing may use the database during the restore)"
for c in "$DJANGO_CONTAINER" "$BOT_CONTAINER"; do
  if container_running "$c"; then
    docker stop "$c" >/dev/null
    info "$c stopped"
  fi
done

if pg_database_exists; then
  existing_tables=$(pg_public_table_count)
  if [ "$existing_tables" -gt 0 ]; then
    if [ $FORCE = 0 ]; then
      die "database '$POSTGRES_DB' already has $existing_tables tables. Re-run with --force to drop it and restore over it."
    fi
    warn "dropping database '$POSTGRES_DB' ($existing_tables tables) because of --force"
  fi
  pg_sql postgres "DROP DATABASE IF EXISTS \"$POSTGRES_DB\" WITH (FORCE)" >/dev/null
fi
log "Creating database '$POSTGRES_DB'"
pg_sql postgres "CREATE DATABASE \"$POSTGRES_DB\" OWNER \"$POSTGRES_USER\"" >/dev/null

log "Restoring db.dump ($(du -h "$BACKUP_DIR/db.dump" | cut -f1))"
docker exec -i "$DB_CONTAINER" pg_restore -U "$POSTGRES_USER" -d "$POSTGRES_DB" \
  --single-transaction --exit-on-error --no-owner --no-acl --no-password \
  < "$BACKUP_DIR/db.dump"

log "Database check (source -> here)"
compare tables_public "$(mf tables_public)" "$(pg_public_table_count)"
for t in "${COUNT_TABLES[@]}"; do
  compare "rows_$t" "$(mf "rows_$t")" "$(pg_table_count "$t")"
done

# --------------------------------------------------------------------- MinIO

log "Preparing $MINIO_SERVICE"
"${COMPOSE[@]}" create "$MINIO_SERVICE"
if container_running "$MINIO_CONTAINER"; then
  docker stop "$MINIO_CONTAINER" >/dev/null
fi

HERE_MINIO_DIGEST=$(image_digest "$MINIO_CONTAINER")
if [ -n "$SRC_MINIO_DIGEST" ] && [ "$HERE_MINIO_DIGEST" != "$SRC_MINIO_DIGEST" ]; then
  warn "the MinIO image here (${HERE_MINIO_DIGEST:-unknown}) is not the build the source server runs ($SRC_MINIO_DIGEST)."
  warn "Newer MinIO releases read this data format, but to run exactly the source build add this line to .env and re-run:"
  warn "  MINIO_IMAGE=minio/minio@$SRC_MINIO_DIGEST"
fi

VOLUME=$(docker inspect -f '{{range .Mounts}}{{if eq .Destination "/data"}}{{.Name}}{{end}}{{end}}' "$MINIO_CONTAINER")
[ -n "$VOLUME" ] || die "no named volume is mounted at $MINIO_DATA_DIR in $MINIO_CONTAINER"

TMP_LISTING=$(mktemp)
trap 'rm -f "$TMP_LISTING"' EXIT
minio_listing_to "$TMP_LISTING"
existing_objects=$(listing_object_count "$TMP_LISTING")
if [ "$existing_objects" -gt 0 ]; then
  if [ $FORCE = 0 ]; then
    die "MinIO volume '$VOLUME' already holds $existing_objects objects. Re-run with --force to wipe it and restore over it."
  fi
  warn "wiping MinIO volume '$VOLUME' ($existing_objects objects) because of --force"
fi

log "Recreating volume '$VOLUME'"
"${COMPOSE[@]}" rm --stop --force "$MINIO_SERVICE" >/dev/null
docker volume rm "$VOLUME" >/dev/null
# compose recreates the declared volume; if the compose file marks it
# external it will not, so create it by hand and try once more.
"${COMPOSE[@]}" create "$MINIO_SERVICE" 2>/dev/null \
  || { docker volume create "$VOLUME" >/dev/null; "${COMPOSE[@]}" create "$MINIO_SERVICE"; }

log "Extracting minio-data.tar.gz ($(du -h "$BACKUP_DIR/minio-data.tar.gz" | cut -f1)) into $MINIO_CONTAINER:$MINIO_DATA_DIR"
# The archive is rooted at "data/", so it is extracted at the container root.
gunzip -c "$BACKUP_DIR/minio-data.tar.gz" | docker cp --archive - "$MINIO_CONTAINER:/"

log "Starting $MINIO_SERVICE"
"${COMPOSE[@]}" up -d "$MINIO_SERVICE"

log "MinIO check (source -> here)"
minio_listing_to "$TMP_LISTING"
compare minio_objects_total "$(mf minio_objects_total)" "$(listing_object_count "$TMP_LISTING")"
for b in $(listing_buckets "$BACKUP_DIR/minio-listing.txt"); do
  compare "minio_objects_$b" "$(mf "minio_objects_$b")" "$(listing_object_count "$TMP_LISTING" "$b")"
done

# Profile pictures are served as plain unsigned URLs, so the anonymous
# download policy on the public bucket has to have come across with
# .minio.sys. HEAD one real object through the S3 port published on this host.
check_public_bucket() {
  local key hostport encoded url status
  key=$(listing_sample_key "$BACKUP_DIR/minio-listing.txt" "$MINIO_BUCKET_PUBLIC")
  if [ -z "$key" ]; then
    info "public bucket '$MINIO_BUCKET_PUBLIC' has no objects; anonymous-download check skipped"
    return 0
  fi
  if ! command -v curl >/dev/null 2>&1; then
    info "curl is not installed; anonymous-download check skipped"
    return 0
  fi
  hostport=$(docker port "$MINIO_CONTAINER" 9000/tcp 2>/dev/null | head -n 1 || true)
  if [ -z "$hostport" ]; then
    info "MinIO port 9000 is not published on this host; anonymous-download check skipped"
    return 0
  fi
  hostport=${hostport/0.0.0.0/127.0.0.1}
  if command -v python3 >/dev/null 2>&1; then
    encoded=$(python3 -c 'import sys, urllib.parse; print(urllib.parse.quote(sys.argv[1]))' "$key")
  else
    encoded=$key
  fi
  url="http://$hostport/$MINIO_BUCKET_PUBLIC/$encoded"
  for _ in $(seq 1 30); do
    curl -sf -o /dev/null "http://$hostport/minio/health/live" && break
    sleep 1
  done
  status=$(curl -s -o /dev/null -w '%{http_code}' -I "$url" 2>/dev/null || true)
  status=${status:-000}
  if [ "$status" = "200" ]; then
    info "anonymous download from '$MINIO_BUCKET_PUBLIC' works (HTTP 200 for $key)"
  else
    warn "HTTP $status for $url"
    warn "the anonymous download policy on '$MINIO_BUCKET_PUBLIC' did not come across; profile pictures will 403 until you run the 'mc anonymous set download' command from docs/MIGRATION_FA.md"
    MISMATCH=1
  fi
}
check_public_bucket

# ------------------------------------------------------------------- summary

echo
if [ $MISMATCH = 1 ]; then
  warn "restore finished with differences (see above). A live snapshot may differ by what was written during the backup; a final one has to match exactly."
else
  log "Restore finished; every count matches the source"
fi
echo
info "Nothing else was started. Next:"
info "  rehearsal (old server still live):   ${COMPOSE[*]} up -d --build $DJANGO_CONTAINER    # not the bot: two pollers on one token conflict"
info "  final cutover (old server stopped):  ${COMPOSE[*]} up -d --build"
info "  then:                                ${COMPOSE[*]} logs -f $DJANGO_CONTAINER"
exit $MISMATCH
