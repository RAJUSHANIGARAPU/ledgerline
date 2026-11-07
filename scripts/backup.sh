#!/bin/sh
# Write a compressed dump to ./backups (or $BACKUP_DIR). Uses the compose "db" service.
set -eu
dir="${BACKUP_DIR:-./backups}"
mkdir -p "$dir"
file="$dir/ledgerline-$(date -u +%Y%m%dT%H%M%SZ).dump"
docker compose exec -T db pg_dump -U ledgerline -Fc ledgerline > "$file.tmp"
mv "$file.tmp" "$file"
echo "backup written: $file"
