#!/bin/sh
# One backup run: dump + manifest from one snapshot, local copy, off-site copy, marker, ping.
set -eu
# shellcheck source=manifest.sh
. /opt/ledgerline/manifest.sh

OPS_DIR="${OPS_DIR:-/ops}"
LOCAL_DIR="${LOCAL_BACKUP_DIR:-/backups}"
KEEP_DAYS="${BACKUP_KEEP_DAYS:-30}"
WORK=/work/current
export RESTIC_CACHE_DIR="${RESTIC_CACHE_DIR:-/work/restic-cache}"

log() { echo "$(date -u +%Y-%m-%dT%H:%M:%SZ) backup: $*"; }

ping_check() { # suffix [message]
  [ -n "${BACKUP_HEALTHCHECK_PING_URL:-}" ] || return 0
  curl -fsS -m 10 --retry 2 -o /dev/null --data-raw "${2:-}" \
    "${BACKUP_HEALTHCHECK_PING_URL%/}$1" || log "warning: health ping failed"
}

psql_owner() { psql -X -At -v ON_ERROR_STOP=1 "$@"; }

HOLD_PID=""
cleanup() {
  rc=$?
  exec 3>&- 2>/dev/null || true
  [ -z "$HOLD_PID" ] || kill "$HOLD_PID" 2>/dev/null || true
  if [ "$rc" -ne 0 ]; then
    log "FAILED (exit $rc)"
    ping_check /fail "backup failed, see container logs"
  fi
}
trap cleanup EXIT

dump_with_manifest() {
  rm -rf "$WORK"; mkdir -p "$WORK"
  mkfifo "$WORK/cmds"
  psql_owner -f "$WORK/cmds" > /dev/null &
  HOLD_PID=$!
  exec 3<> "$WORK/cmds"
  printf '%s\n' \
    "BEGIN ISOLATION LEVEL REPEATABLE READ READ ONLY;" \
    "\\o $WORK/snapshot" "SELECT pg_export_snapshot();" \
    "\\o $WORK/counts" "\\i /opt/ledgerline/table-counts.sql" "\\o" >&3
  i=0
  while [ ! -s "$WORK/snapshot" ] || [ ! -s "$WORK/counts" ]; do
    i=$((i + 1)); [ "$i" -le 100 ] || { log "no snapshot or table list (is the schema migrated?)"; return 1; }
    sleep 0.2
  done
  pg_dump -Fc --snapshot="$(cat "$WORK/snapshot")" -f "$WORK/ledgerline.dump"
  exec 3>&-
  wait "$HOLD_PID"; HOLD_PID=""
  revision="$(psql_owner -c 'SELECT version_num FROM alembic_version')"
  write_manifest "$WORK/counts" "$WORK/ledgerline.dump" "$revision" "$WORK/manifest.txt"
  rm -f "$WORK/cmds" "$WORK/snapshot" "$WORK/counts"
}

local_copy() {
  stamp="$(date -u +%Y%m%dT%H%M%SZ)"
  mkdir -p "$LOCAL_DIR"
  cp "$WORK/ledgerline.dump" "$LOCAL_DIR/ledgerline-$stamp.dump.tmp"
  mv "$LOCAL_DIR/ledgerline-$stamp.dump.tmp" "$LOCAL_DIR/ledgerline-$stamp.dump"
  cp "$WORK/manifest.txt" "$LOCAL_DIR/ledgerline-$stamp.manifest.txt"
  find "$LOCAL_DIR" -name 'ledgerline-*.dump' -mtime +"$KEEP_DAYS" -delete
  find "$LOCAL_DIR" -name 'ledgerline-*.manifest.txt' -mtime +"$KEEP_DAYS" -delete
  log "local copy: $LOCAL_DIR/ledgerline-$stamp.dump"
}

offsite_copy() {
  if ! err="$(restic cat config 2>&1 > /dev/null)"; then
    case "$err" in
      *"Is there a repository"*|*"does not exist"*) restic init ;;
      *) echo "$err" >&2; return 1 ;;
    esac
  fi
  restic backup --host ledgerline --tag ledgerline --quiet "$WORK"
  restic forget --host ledgerline --tag ledgerline --group-by host,tags \
    --keep-daily 14 --keep-weekly 8 --keep-monthly 24 --keep-yearly 8 --prune --quiet
  log "off-site snapshot stored"
  if [ "$(date -u +%u)" = "${BACKUP_CHECK_WEEKDAY:-7}" ]; then
    restic check --read-data-subset=5%
    log "restic check passed"
  fi
}

main() {
  ping_check /start
  offsite=0
  dump_with_manifest
  local_copy
  if [ -n "${RESTIC_REPOSITORY:-}" ] && [ -n "${RESTIC_PASSWORD:-}${RESTIC_PASSWORD_FILE:-}" ]; then
    offsite_copy
    offsite=1
  else
    log "WARNING: RESTIC_REPOSITORY/RESTIC_PASSWORD not set, no off-site copy"
  fi
  printf 'epoch=%s\noffsite=%s\n' "$(date -u +%s)" "$offsite" > "$OPS_DIR/last_backup_ok.tmp"
  mv "$OPS_DIR/last_backup_ok.tmp" "$OPS_DIR/last_backup_ok"
  ping_check "" "ok offsite=$offsite"
  log "done (offsite=$offsite)"
}

main
