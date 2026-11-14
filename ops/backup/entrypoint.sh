#!/bin/sh
# Scheduler: one run at start when the last success is stale, then daily at BACKUP_HOUR_UTC.
# BACKUP_ONCE=1 runs a single backup and exits with its status.
set -u
OPS_DIR="${OPS_DIR:-/ops}"
mkdir -p "$OPS_DIR" /work
# The app and worker run as uid 10001 and write the worker heartbeat into the same volume.
chown 10001:10001 "$OPS_DIR" 2>/dev/null || true
chmod 0755 "$OPS_DIR"

if [ "${BACKUP_ONCE:-0}" = "1" ]; then
  exec /opt/ledgerline/backup.sh
fi

stale() {
  marker="$OPS_DIR/last_backup_ok"
  [ -f "$marker" ] || return 0
  last="$(sed -n 's/^epoch=//p' "$marker")"
  [ $(( $(date -u +%s) - ${last:-0} )) -gt 86400 ] && return 0
  # A repository was configured after the last (local-only) run: copy off-site now instead of
  # waiting for the next scheduled hour.
  if [ -n "${RESTIC_REPOSITORY:-}" ] && [ -n "${RESTIC_PASSWORD:-}${RESTIC_PASSWORD_FILE:-}" ]; then
    [ "$(sed -n 's/^offsite=//p' "$marker")" = "1" ] || return 0
  fi
  return 1
}

failed=0
if stale; then /opt/ledgerline/backup.sh || failed=1; fi
while true; do
  if [ "$failed" = "1" ]; then
    echo "backup failed, retrying in 1h"
    wait=3600
  else
    wait=$(( (${BACKUP_HOUR_UTC:-2} * 3600 - $(date -u +%s) % 86400 + 86400) % 86400 + 1 ))
  fi
  sleep "$wait"
  failed=0
  /opt/ledgerline/backup.sh || failed=1
done
