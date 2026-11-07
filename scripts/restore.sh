#!/bin/sh
# Restore a dump into the compose database. Replaces existing data.
# Usage: scripts/restore.sh [--yes] backups/ledgerline-YYYYMMDDTHHMMSSZ.dump
set -eu
yes=0
if [ "${1:-}" = "--yes" ]; then yes=1; shift; fi
file="${1:?usage: restore.sh [--yes] <dump file>}"
[ -f "$file" ] || { echo "no such file: $file" >&2; exit 1; }
if [ "$yes" -ne 1 ]; then
  printf 'This replaces ALL data in the ledgerline database with %s. Type "restore" to continue: ' "$file"
  read -r answer
  [ "$answer" = "restore" ] || { echo "aborted"; exit 1; }
fi
docker compose stop app
docker compose exec -T db pg_restore -U ledgerline -d ledgerline --clean --if-exists --no-owner < "$file"
docker compose start app
echo "restored from $file"
