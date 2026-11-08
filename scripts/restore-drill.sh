#!/bin/sh
# Restore the latest (or a given) off-site snapshot into a throw-away compose project, check
# row counts against the manifest taken at backup time, start the app and hit /healthz.
# Needs only Docker. Prints "RESULT: PASS" (exit 0) or "RESULT: FAIL - <reason>" (exit 1).
#
# Usage: scripts/restore-drill.sh [--snapshot <id|latest>] [--env-file <file>] [--keep]
# Repository settings come from the environment or the env file: RESTIC_REPOSITORY,
# RESTIC_PASSWORD, AWS_ACCESS_KEY_ID, AWS_SECRET_ACCESS_KEY, AWS_DEFAULT_REGION.
# A RESTIC_REPOSITORY that is an absolute path is a local (file) repository.
# DRILL_SKIP_APP=1 skips the app start (database restore and row counts only).
set -eu

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
# shellcheck source=../ops/backup/manifest.sh
. "$ROOT/ops/backup/manifest.sh"

SNAPSHOT=latest
ENV_FILE="$ROOT/.env"
KEEP=0
while [ $# -gt 0 ]; do
  case "$1" in
    --snapshot) SNAPSHOT="${2:?--snapshot needs a value}"; shift 2 ;;
    --env-file) ENV_FILE="${2:?--env-file needs a value}"; shift 2 ;;
    --keep) KEEP=1; shift ;;
    -h|--help) sed -n '2,11p' "$0"; exit 0 ;;
    *) echo "unknown argument: $1" >&2; exit 2 ;;
  esac
done

RESTIC_IMAGE="${RESTIC_IMAGE:-restic/restic:0.19.1}"
PROJECT="${DRILL_PROJECT:-ledgerline-drill-$$}"
TMP="$(mktemp -d)"
STARTED=0

say() { echo "[drill] $*"; }

dc() { docker compose -p "$PROJECT" -f "$ROOT/docker-compose.yml" "$@"; }

cleanup() {
  rc=$?
  if [ "$STARTED" = "1" ] && [ "$KEEP" != "1" ]; then dc down -v --remove-orphans >/dev/null 2>&1 || true; fi
  [ "$KEEP" != "1" ] || [ "$STARTED" != "1" ] || say "kept project $PROJECT (docker compose -p $PROJECT down -v)"
  docker run --rm --entrypoint sh -v "$TMP:/t" "$RESTIC_IMAGE" -c 'rm -rf /t/* /t/.[!.]*' >/dev/null 2>&1 || true
  rm -rf "$TMP"
  return "$rc"
}
trap cleanup EXIT

fail() { echo "RESULT: FAIL - $*"; exit 1; }

# Run a command with its output captured; show the tail only when it fails.
quiet() {
  "$@" > "$TMP/last.log" 2>&1 || { tail -n 20 "$TMP/last.log"; return 1; }
}

command -v docker >/dev/null 2>&1 || fail "docker not found"

load_env() { # copy RESTIC_* and AWS_* from the env file unless already set
  [ -f "$ENV_FILE" ] || return 0
  while IFS= read -r line; do
    case "$line" in
      RESTIC_*=*|AWS_*=*)
        key="${line%%=*}"; value="${line#*=}"
        eval "[ -n \"\${$key:-}\" ]" || export "$key=$value" ;;
    esac
  done < "$ENV_FILE"
}
load_env
: "${RESTIC_REPOSITORY:?RESTIC_REPOSITORY not set (environment or $ENV_FILE)}"
: "${RESTIC_PASSWORD:?RESTIC_PASSWORD not set (environment or $ENV_FILE)}"

REPO_MOUNT=""
REPO_IN_CONTAINER="$RESTIC_REPOSITORY"
case "$RESTIC_REPOSITORY" in
  /*) REPO_MOUNT="-v $RESTIC_REPOSITORY:/repo"; REPO_IN_CONTAINER=/repo ;;
esac

restic_docker() {
  # shellcheck disable=SC2086
  docker run --rm $REPO_MOUNT -v "$TMP:/restore" \
    -e RESTIC_PASSWORD -e AWS_ACCESS_KEY_ID -e AWS_SECRET_ACCESS_KEY -e AWS_DEFAULT_REGION \
    -e "RESTIC_REPOSITORY=$REPO_IN_CONTAINER" "$RESTIC_IMAGE" "$@"
}

say "restoring snapshot $SNAPSHOT from the repository"
if [ "$SNAPSHOT" = "latest" ]; then
  restic_docker restore latest --tag ledgerline --host ledgerline --target /restore --quiet \
    || fail "restic restore failed (repository, password or no snapshot)"
else
  restic_docker restore "$SNAPSHOT" --target /restore --quiet || fail "restic restore failed"
fi
DUMP="$TMP/work/current/ledgerline.dump"
MANIFEST="$TMP/work/current/manifest.txt"
[ -s "$DUMP" ] || fail "snapshot holds no ledgerline.dump"
[ -s "$MANIFEST" ] || fail "snapshot holds no manifest.txt"

expected_sha="$(manifest_value "$MANIFEST" dump_sha256)"
[ "$(sha256_of "$DUMP")" = "$expected_sha" ] || fail "dump checksum differs from the manifest"
say "manifest from $(manifest_value "$MANIFEST" created_at), schema revision $(manifest_value "$MANIFEST" alembic_revision)"

# Random credentials: the drill stack shares nothing with production.
rand() { od -An -N24 -tx1 /dev/urandom | tr -d ' \n'; }
POSTGRES_PASSWORD="$(rand)"; LEDGERLINE_APP_DB_PASSWORD="$(rand)"; LEDGERLINE_SECRET_KEY="$(rand)"
export POSTGRES_PASSWORD LEDGERLINE_APP_DB_PASSWORD LEDGERLINE_SECRET_KEY
export LEDGERLINE_COOKIE_SECURE=false LEDGERLINE_BIND="127.0.0.1:"
export LEDGERLINE_INITIAL_OWNER_EMAIL="" LEDGERLINE_INITIAL_PASSWORD=""

STARTED=1
say "starting a clean database in project $PROJECT"
quiet dc up -d --wait db || fail "database did not start"
quiet dc run --rm migrate || fail "initial migration failed"

say "restoring the dump"
dc exec -T db pg_restore -U ledgerline -d ledgerline --clean --if-exists --no-owner < "$DUMP" \
  || fail "pg_restore reported errors"

say "comparing row counts with the manifest"
dc exec -T db psql -U ledgerline -d ledgerline -X -At -v ON_ERROR_STOP=1 \
  < "$ROOT/ops/backup/table-counts.sql" > "$TMP/counts.actual" || fail "could not count restored rows"
if ! diff_out="$(compare_counts "$MANIFEST" "$TMP/counts.actual")"; then
  echo "$diff_out"
  fail "row counts differ from the manifest"
fi
say "$(manifest_counts "$MANIFEST" | wc -l | tr -d ' ') tables match ($(awk -F'\t' '{s+=$2} END {print s+0}' "$TMP/counts.actual") rows)"

say "running migrations on the restored database"
quiet dc run --rm migrate || fail "migration of the restored database failed"

if [ "${DRILL_SKIP_APP:-0}" = "1" ]; then
  say "app start skipped (DRILL_SKIP_APP=1)"
else
  say "starting the app"
  quiet dc up -d --wait app || fail "app did not become healthy"
  addr="$(dc port app 8000)" || fail "app port not published"
  body="$(curl -fsS -m 10 "http://$addr/healthz")" || fail "/healthz not reachable at $addr"
  case "$body" in *'"ok"'*) say "/healthz: $body" ;; *) fail "/healthz returned: $body" ;; esac
fi

echo "RESULT: PASS"
