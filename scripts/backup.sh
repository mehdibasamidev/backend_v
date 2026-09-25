#!/usr/bin/env bash
#
# scripts/backup.sh - snapshot this server's PostgreSQL and MinIO data (plus
# .env, haproxy.cfg and the crontab) into backups/<timestamp>/ so that
# scripts/restore.sh can load it on another VPS. Persian walkthrough:
# docs/MIGRATION_FA.md.
#
# Run it on the server that HAS the data, from any directory:
#
#   scripts/backup.sh            live snapshot, nothing is stopped. Good for a
#                                rehearsal; anything written while it runs may
#                                be missing or half-copied.
#   scripts/backup.sh --final    cutover: stops django, the bot and MinIO
#                                first and leaves them stopped. django and the
#                                bot also get restart=no, so a reboot of the
#                                old VPS cannot bring a second bot poller up.
#   scripts/backup.sh --out DIR  write into DIR instead of backups/<timestamp>
#   scripts/backup.sh --yes      skip the confirmation prompt of --final
#
# Output (files 600, directory 700: env.backup holds every production secret):
#   db.dump             pg_dump custom format of $POSTGRES_DB
#   minio-data.tar.gz   raw copy of the MinIO data dir: both buckets and the
#                       bucket policies (the anonymous download on `media`)
#   minio-listing.txt   `tar t` of that archive, used for the object counts
#   env.backup          copy of .env
#   haproxy.cfg         copy of /etc/haproxy/haproxy.cfg when readable
#   crontab.txt         `crontab -l` of the current user
#   migrations.txt      latest applied migration per Django app
#   manifest.txt        versions, images, row and object counts (key=value)
#   SHA256SUMS          checksums, verified by restore.sh
set -euo pipefail
shopt -s inherit_errexit

SCRIPT_DIR=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
PROJECT_DIR=$(cd "$SCRIPT_DIR/.." && pwd)
# shellcheck source=scripts/_lib.sh
. "$SCRIPT_DIR/_lib.sh"

FINAL=0
YES=0
OUT=""
while [ $# -gt 0 ]; do
  case $1 in
    --final) FINAL=1 ;;
    --yes|-y) YES=1 ;;
    --out) [ $# -ge 2 ] || die "--out needs a directory"; OUT=$2; shift ;;
    --out=*) OUT=${1#--out=} ;;
    -h|--help) sed -n '3,/^set -euo/p' "$0" | sed '$d' | sed 's/^# \{0,1\}//'; exit 0 ;;
    *) die "unknown option: $1 (see --help)" ;;
  esac
  shift
done

require_cmd docker
require_cmd gzip
require_cmd tar
require_cmd sha256sum
cd "$PROJECT_DIR"
load_env

container_running "$DB_CONTAINER" || die "$DB_CONTAINER is not running; the dump is taken through it (docker ps)"
container_exists "$MINIO_CONTAINER" || die "$MINIO_CONTAINER does not exist; its data dir is what gets archived"

STAMP=$(date -u +%Y-%m-%dT%H%M%SZ)
OUT=${OUT:-$PROJECT_DIR/backups/$STAMP}
if [ -e "$OUT" ] && [ -n "$(ls -A "$OUT" 2>/dev/null)" ]; then
  die "$OUT already exists and is not empty"
fi
umask 077
mkdir -p "$OUT"
OUT=$(cd "$OUT" && pwd)

# ------------------------------------------------------------ stop (--final)

if [ $FINAL = 1 ]; then
  warn "--final stops $DJANGO_CONTAINER, $BOT_CONTAINER and $MINIO_CONTAINER on THIS server and leaves them stopped."
  if [ $YES = 0 ]; then
    printf 'Type "yes" to continue: '
    read -r answer
    [ "$answer" = "yes" ] || die "aborted"
  fi
  log "Stopping the application (django + bot)"
  for c in "$DJANGO_CONTAINER" "$BOT_CONTAINER"; do
    if container_exists "$c"; then
      docker update --restart=no "$c" >/dev/null
      docker stop "$c" >/dev/null
      info "$c stopped, restart policy set to 'no'"
    fi
  done
else
  warn "live snapshot: the app keeps running, so writes made during the backup may be missing. Use --final for the real cutover."
fi

# ------------------------------------------------------------------ manifest

MANIFEST=$OUT/manifest.txt
m() { printf '%s=%s\n' "$1" "$2" >> "$MANIFEST"; }
: > "$MANIFEST"
m backup_created_at "$STAMP"
m backup_mode "$([ $FINAL = 1 ] && echo final || echo live)"
m source_hostname "$(hostname)"
m source_project_dir "$PROJECT_DIR"
m source_git_commit "$(git -C "$PROJECT_DIR" rev-parse HEAD 2>/dev/null || echo unknown)"
m source_git_branch "$(git -C "$PROJECT_DIR" rev-parse --abbrev-ref HEAD 2>/dev/null || echo unknown)"
m docker_version "$(docker version --format '{{.Server.Version}}' 2>/dev/null || echo unknown)"
m image_db "$(image_ref "$DB_CONTAINER")"
m image_minio "$(image_ref "$MINIO_CONTAINER")"
m image_minio_digest "$(image_digest "$MINIO_CONTAINER")"
m image_redis "$(image_ref "$REDIS_CONTAINER")"
m postgres_db "$POSTGRES_DB"
m postgres_user "$POSTGRES_USER"
m minio_bucket_public "$MINIO_BUCKET_PUBLIC"
m minio_bucket_private "$MINIO_BUCKET_PRIVATE"

# ---------------------------------------------------------------- PostgreSQL

log "Dumping PostgreSQL database '$POSTGRES_DB' as $POSTGRES_USER"
if ! pg_database_exists; then
  existing=$(pg_database_list 2>/dev/null || echo "?")
  die "database '$POSTGRES_DB' does not exist in $DB_CONTAINER (it has: $existing). Fix POSTGRES_DB in .env; production has historically used fitness_db, see CLAUDE.md."
fi
m postgres_server_version "$(pg_sql postgres 'SHOW server_version')"
docker exec "$DB_CONTAINER" pg_dump -U "$POSTGRES_USER" --format=custom --no-password "$POSTGRES_DB" > "$OUT/db.dump"
m db_dump_bytes "$(stat -c %s "$OUT/db.dump")"
m tables_public "$(pg_public_table_count)"
for t in "${COUNT_TABLES[@]}"; do
  m "rows_$t" "$(pg_table_count "$t")"
done
pg_sql "$POSTGRES_DB" "SELECT app || ': ' || name FROM django_migrations
  WHERE id IN (SELECT max(id) FROM django_migrations GROUP BY app) ORDER BY app" \
  > "$OUT/migrations.txt" 2>/dev/null || echo "(django_migrations table not found)" > "$OUT/migrations.txt"
info "db.dump: $(du -h "$OUT/db.dump" | cut -f1), $(grep -c . "$OUT/migrations.txt") apps in migrations.txt"

# --------------------------------------------------------------------- MinIO

if [ $FINAL = 1 ] && container_running "$MINIO_CONTAINER"; then
  log "Stopping $MINIO_CONTAINER so the archive is consistent"
  docker stop "$MINIO_CONTAINER" >/dev/null
fi
log "Archiving the MinIO data dir ($MINIO_CONTAINER:$MINIO_DATA_DIR)"
# gzip -1: the payload is mostly images and PDFs that are already compressed.
docker cp "$MINIO_CONTAINER:$MINIO_DATA_DIR" - | gzip -1 > "$OUT/minio-data.tar.gz"
tar tzf "$OUT/minio-data.tar.gz" > "$OUT/minio-listing.txt"
m minio_archive_bytes "$(stat -c %s "$OUT/minio-data.tar.gz")"
m minio_buckets "$(listing_buckets "$OUT/minio-listing.txt" | paste -sd, -)"
m minio_objects_total "$(listing_object_count "$OUT/minio-listing.txt")"
for b in $(listing_buckets "$OUT/minio-listing.txt"); do
  m "minio_objects_$b" "$(listing_object_count "$OUT/minio-listing.txt" "$b")"
done
for b in "$MINIO_BUCKET_PUBLIC" "$MINIO_BUCKET_PRIVATE"; do
  listing_buckets "$OUT/minio-listing.txt" | grep -qx -- "$b" \
    || warn "bucket '$b' (named in .env) is not in the archive"
done
info "minio-data.tar.gz: $(du -h "$OUT/minio-data.tar.gz" | cut -f1), $(listing_object_count "$OUT/minio-listing.txt") objects"

# ----------------------------------------------------------- config snapshots

log "Copying .env, haproxy.cfg and the crontab"
cp "$ENV_FILE" "$OUT/env.backup"
if [ -r /etc/haproxy/haproxy.cfg ]; then
  cp /etc/haproxy/haproxy.cfg "$OUT/haproxy.cfg"
else
  info "/etc/haproxy/haproxy.cfg is not readable, skipped (sudo cp it yourself if needed)"
fi
crontab -l > "$OUT/crontab.txt" 2>/dev/null || echo "(no crontab for $(id -un))" > "$OUT/crontab.txt"

# ----------------------------------------------------------------- checksums

log "Writing SHA256SUMS"
(
  cd "$OUT"
  files="db.dump minio-data.tar.gz minio-listing.txt env.backup manifest.txt migrations.txt crontab.txt"
  [ -f haproxy.cfg ] && files="$files haproxy.cfg"
  # shellcheck disable=SC2086
  sha256sum $files > SHA256SUMS
)

# ------------------------------------------------------------------- summary

echo
log "Backup complete: $OUT ($(du -sh "$OUT" | cut -f1))"
sed 's/^/    /' "$MANIFEST"
echo
info "Next: copy the directory to the new server, for example"
info "  rsync -avz --progress -e ssh '$OUT' deploy@NEW_SERVER_IP:$PROJECT_DIR/backups/"
info "then run  scripts/restore.sh backups/$(basename "$OUT")  there. Walkthrough: docs/MIGRATION_FA.md"
if [ $FINAL = 1 ]; then
  echo
  warn "The app on this server is STOPPED and will not start on its own. To bring it back (rollback):"
  info "  docker update --restart=always $DJANGO_CONTAINER $BOT_CONTAINER"
  info "  docker start $MINIO_CONTAINER $DJANGO_CONTAINER $BOT_CONTAINER"
fi
