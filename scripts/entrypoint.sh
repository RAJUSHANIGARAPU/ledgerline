#!/bin/sh
set -eu

: "${LEDGERLINE_SECRET_KEY:?LEDGERLINE_SECRET_KEY must be set}"

ledgerline migrate

if [ -n "${LEDGERLINE_INITIAL_OWNER_EMAIL:-}" ] && [ -n "${LEDGERLINE_INITIAL_PASSWORD:-}" ]; then
  ledgerline create-user --email "$LEDGERLINE_INITIAL_OWNER_EMAIL" --role owner \
    || echo "initial owner already exists, skipping"
fi

exec "$@"
