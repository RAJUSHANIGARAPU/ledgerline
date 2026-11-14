#!/bin/sh
# Restore the latest (or a given) off-site snapshot into a throw-away compose project, check
# row counts against the manifest taken at backup time, start the app, then verify the restored
# system: /healthz and /healthz/deep, the app role is neither superuser nor BYPASSRLS, every
# table with a tenant policy has row-level security enabled and forced, and two tenants cannot
# see each other's rows as the app role. One PASS/FAIL/SKIP line per check.
# Needs only Docker. Prints "RESULT: PASS" (exit 0) or "RESULT: FAIL - <reason>" (exit 1).
#
# Usage: scripts/restore-drill.sh [--snapshot <id|latest>] [--env-file <file>] [--keep]
# Repository settings come from the environment or the env file: RESTIC_REPOSITORY,
# RESTIC_PASSWORD, AWS_ACCESS_KEY_ID, AWS_SECRET_ACCESS_KEY, AWS_DEFAULT_REGION.
# A RESTIC_REPOSITORY that is an absolute path is a local (file) repository.
# The legal check of /healthz/deep (operator identity, drafting markers) does not fail the drill.
# DRILL_SKIP_APP=1 skips the app start and the HTTP checks (database checks still run).
# DRILL_MIN_FREE_DISK_PERCENT=<1-99> sets the /healthz/deep disk threshold for the drill stack, for
# a machine whose Docker disk is fuller than production (default: the application's 10).
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
    -h|--help) sed -n '2,17p' "$0"; exit 0 ;;
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
if [ -n "${DRILL_MIN_FREE_DISK_PERCENT:-}" ]; then
  export LEDGERLINE_HEALTH_MIN_FREE_DISK_PERCENT="$DRILL_MIN_FREE_DISK_PERCENT"
fi

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

# --- Verification of the restored system: every check prints one PASS/FAIL/SKIP line. ---
FAILED=""
verdict() { # verdict <PASS|FAIL|SKIP> <name> <detail>
  printf '[drill] %-4s %s: %s\n' "$1" "$2" "$3"
  [ "$1" != "FAIL" ] || FAILED="${FAILED:+$FAILED, }$2"
}

owner_psql() { dc exec -T db psql -U ledgerline -d ledgerline -X -At -v ON_ERROR_STOP=1 "$@"; }
app_psql() {
  dc exec -T -e PGPASSWORD="$LEDGERLINE_APP_DB_PASSWORD" db \
    psql -h 127.0.0.1 -U ledgerline_app -d ledgerline -X -At -v ON_ERROR_STOP=1 "$@"
}

check_http() {
  addr="$(dc port app 8000)" || { verdict FAIL healthz "app port not published"; return; }
  body="$(curl -fsS -m 10 "http://$addr/healthz" 2>&1)" || { verdict FAIL healthz "not reachable at $addr"; return; }
  case "$body" in *'"ok"'*) verdict PASS healthz "$body" ;; *) verdict FAIL healthz "returned: $body" ;; esac

  # The deep check needs the worker heartbeat and a backup marker in the ops volume. The drill
  # starts the worker and writes the marker itself (the backup job is not part of the drill; the
  # restore from the off-site repository above is the proof that a copy exists there).
  quiet dc run --rm --no-deps --entrypoint sh app -c \
    "printf 'epoch=%s\noffsite=1\n' \"\$(date -u +%s)\" > /var/lib/ledgerline/ops/last_backup_ok" \
    || { verdict FAIL healthz-deep "could not write the backup marker"; return; }
  quiet dc up -d worker || { verdict FAIL healthz-deep "worker did not start"; return; }
  tries=0; code=000
  while [ "$tries" -lt 30 ]; do
    code="$(curl -sS -m 10 -o "$TMP/deep.json" -w '%{http_code}' "http://$addr/healthz/deep" 2>/dev/null || echo 000)"
    [ "$code" = "200" ] && break
    tries=$((tries + 1)); sleep 2
  done
  if [ "$code" = "200" ]; then verdict PASS healthz-deep "HTTP 200 $(cat "$TMP/deep.json")"
  elif [ "$code" = "503" ] && [ "$(grep -o '"ok":false' "$TMP/deep.json" | wc -l)" -eq 1 ] \
    && grep -q '"legal":{"ok":false' "$TMP/deep.json"; then
    # Legal-page readiness (operator identity, drafting markers) is a deployment setting, not a
    # property of the restored data: report it, but do not fail the restore on it.
    verdict PASS healthz-deep "restore checks ok; legal check still open (operator identity or drafting markers), see /healthz/deep on the live system"
  else verdict FAIL healthz-deep "HTTP $code $(cat "$TMP/deep.json" 2>/dev/null)"; fi
}

check_app_role() {
  flags="$(owner_psql -c "SELECT rolsuper::text || ' ' || rolbypassrls::text FROM pg_roles WHERE rolname = 'ledgerline_app'")" \
    || { verdict FAIL app-role "could not read pg_roles"; return; }
  case "$flags" in
    "false false") verdict PASS app-role "ledgerline_app: superuser=false bypassrls=false" ;;
    "") verdict FAIL app-role "role ledgerline_app does not exist" ;;
    *) verdict FAIL app-role "ledgerline_app: superuser/bypassrls = $flags" ;;
  esac
}

TENANT_TABLES=""
check_rls() {
  # Every public table that carries a row-level security policy is a tenant table; it must also
  # have RLS enabled and forced (FORCE makes the owner role obey it too). Derived from the catalog.
  TENANT_TABLES="$(owner_psql -c "SELECT c.relname FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
    WHERE n.nspname = 'public' AND c.relkind IN ('r','p')
      AND EXISTS (SELECT 1 FROM pg_policy p WHERE p.polrelid = c.oid) ORDER BY 1")" \
    || { verdict FAIL rls "could not read the catalog"; return; }
  [ -n "$TENANT_TABLES" ] || { verdict FAIL rls "no table with a row-level security policy found"; return; }
  total="$(printf '%s\n' "$TENANT_TABLES" | wc -l | tr -d ' ')"
  bad="$(owner_psql -c "SELECT string_agg(c.relname, ', ' ORDER BY c.relname) FROM pg_class c
    JOIN pg_namespace n ON n.oid = c.relnamespace
    WHERE n.nspname = 'public' AND c.relkind IN ('r','p')
      AND EXISTS (SELECT 1 FROM pg_policy p WHERE p.polrelid = c.oid)
      AND NOT (c.relrowsecurity AND c.relforcerowsecurity)")" || { verdict FAIL rls "catalog query failed"; return; }
  if [ -z "$bad" ]; then verdict PASS rls "$total tenant tables, RLS enabled and forced on all"
  else verdict FAIL rls "enabled+forced missing on: $bad"; fi
  loose="$(owner_psql -c "SELECT string_agg(c.relname, ', ' ORDER BY c.relname) FROM pg_class c
    JOIN pg_namespace n ON n.oid = c.relnamespace
    WHERE n.nspname = 'public' AND c.relkind IN ('r','p')
      AND EXISTS (SELECT 1 FROM pg_attribute a WHERE a.attrelid = c.oid AND a.attname = 'tenant_id' AND NOT a.attisdropped)
      AND NOT EXISTS (SELECT 1 FROM pg_policy p WHERE p.polrelid = c.oid)")" || loose=""
  [ -z "$loose" ] || say "note: tables with a tenant_id column but no policy (expected to be global lookups): $loose"
}

check_isolation() {
  [ -n "$TENANT_TABLES" ] || { verdict SKIP tenant-isolation "no tenant tables found"; return; }
  with_col="$(owner_psql -c "SELECT c.relname FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
    WHERE n.nspname = 'public' AND c.relkind IN ('r','p')
      AND EXISTS (SELECT 1 FROM pg_policy p WHERE p.polrelid = c.oid)
      AND EXISTS (SELECT 1 FROM pg_attribute a WHERE a.attrelid = c.oid AND a.attname = 'tenant_id' AND NOT a.attisdropped)
    ORDER BY 1")" || { verdict FAIL tenant-isolation "catalog query failed"; return; }
  pair="$(owner_psql -c "SELECT tenant_id FROM administrations GROUP BY tenant_id ORDER BY tenant_id LIMIT 2")" \
    || { verdict FAIL tenant-isolation "could not list tenants"; return; }
  a="$(printf '%s\n' "$pair" | sed -n 1p)"; b="$(printf '%s\n' "$pair" | sed -n 2p)"
  if [ -z "$b" ]; then
    verdict SKIP tenant-isolation "needs two tenants with data, the snapshot has fewer"; return
  fi
  leaks=0; hidden=0; own_ok=1
  for t in $with_col; do
    owner="$(owner_psql -c "SELECT count(*) FILTER (WHERE tenant_id = $a) || '|' || count(*) FILTER (WHERE tenant_id = $b) FROM $t")" \
      || { verdict FAIL tenant-isolation "owner count on $t failed"; return; }
    seen="$(app_psql -c "SELECT set_config('app.tenant_id', '$a', false)" \
      -c "SELECT count(*) FILTER (WHERE tenant_id = $a) || '|' || count(*) FILTER (WHERE tenant_id = $b) || '|' || count(*) FILTER (WHERE tenant_id <> $a) FROM $t" \
      2>&1 | tail -n 1)"
    case "$seen" in
      *[!0-9\|]*|"") verdict FAIL tenant-isolation "app role query on $t failed: $seen"; return ;;
    esac
    seen_own="${seen%%|*}"; rest="${seen#*|}"; seen_other="${rest%%|*}"; seen_any="${rest#*|}"
    [ "$seen_other" = "0" ] && [ "$seen_any" = "0" ] \
      || { leaks=$((leaks + 1)); say "tenant $a sees foreign rows in $t (tenant $b: $seen_other, any other: $seen_any)"; }
    [ "$seen_own" = "${owner%%|*}" ] || own_ok=0
    [ "${owner#*|}" = "0" ] || hidden=$((hidden + 1))
  done
  if [ "$leaks" -gt 0 ]; then verdict FAIL tenant-isolation "tenant $a sees tenant $b rows in $leaks table(s)"
  elif [ "$own_ok" = "0" ]; then verdict FAIL tenant-isolation "tenant $a does not see all of its own rows"
  elif [ "$hidden" -eq 0 ]; then verdict SKIP tenant-isolation "tenants $a and $b: tenant $b has no rows to hide"
  else verdict PASS tenant-isolation "tenants $a and $b: tenant $a sees 0 of tenant $b's rows in $hidden table(s) and all of its own"; fi
}

if [ "${DRILL_SKIP_APP:-0}" = "1" ]; then
  say "app start skipped (DRILL_SKIP_APP=1)"
  verdict SKIP healthz "app not started"
  verdict SKIP healthz-deep "app not started"
else
  say "starting the app"
  quiet dc up -d --wait app || fail "app did not become healthy"
  check_http
fi
check_app_role
check_rls
check_isolation

if [ -n "$FAILED" ]; then fail "failed checks: $FAILED"; fi
echo "RESULT: PASS"
