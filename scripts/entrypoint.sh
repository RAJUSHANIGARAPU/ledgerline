#!/bin/sh
set -eu

# Only the one-shot migrate service holds the owner credentials. It migrates, sets up the
# restricted role and exits (its command is a no-op); app and worker never see the owner URL.
if [ -n "${LEDGERLINE_MIGRATION_DATABASE_URL:-}" ]; then
  : "${LEDGERLINE_APP_DB_PASSWORD:?LEDGERLINE_APP_DB_PASSWORD must be set}"
  ledgerline migrate
  ledgerline ensure-app-role
  exec "$@"
fi

: "${LEDGERLINE_SECRET_KEY:?LEDGERLINE_SECRET_KEY must be set}"

if [ -n "${LEDGERLINE_INITIAL_OWNER_EMAIL:-}" ] && [ -n "${LEDGERLINE_INITIAL_PASSWORD:-}" ]; then
  ledgerline create-user --email "$LEDGERLINE_INITIAL_OWNER_EMAIL" --role owner --tenant 1 \
    || echo "initial owner already exists, skipping"
fi

exec "$@"
