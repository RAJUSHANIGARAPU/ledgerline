#!/bin/sh
# Update a running Ledgerline stack: back up, pull, rebuild, migrate, health check, and roll
# back to the previous code and image when the new version does not become healthy.
# Then compare /healthz/deep with its state before the update: a check that was ok before and
# is not ok after stops the script with exit code 2 and leaves the new version running (a
# rollback cannot cross a migration, so the operator decides).
#
# Usage (as the deploy user, from anywhere): deploy/hetzner/update.sh [git-ref]
#   git-ref  tag, branch or commit to deploy; default: the current branch, fast-forwarded.
# Exit codes: 0 deployed and healthy, 1 not deployed or rolled back, 2 deployed but a deep
#             check regressed.
# Environment: HEALTH_URL (default http://127.0.0.1:8000/healthz), HEALTH_TIMEOUT seconds (120),
#              DEEP_HEALTH_URL (default HEALTH_URL with /deep appended),
#              SKIP_BACKUP=1 to skip the pre-update backup.
#              CI_REPO (owner/name on GitHub; default: taken from the origin URL),
#              ALLOW_UNVERIFIED_DEPLOY="<reason>" to deploy a commit without a green CI run.
set -eu

ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
cd "$ROOT"
HEALTH_URL="${HEALTH_URL:-http://127.0.0.1:8000/healthz}"
HEALTH_TIMEOUT="${HEALTH_TIMEOUT:-120}"
DEEP_HEALTH_URL="${DEEP_HEALTH_URL:-${HEALTH_URL%/}/deep}"
TARGET="${1:-}"

say() { echo "[update] $*"; }

wait_healthy() {
  waited=0
  while [ "$waited" -lt "$HEALTH_TIMEOUT" ]; do
    if curl -fsS -m 5 "$HEALTH_URL" 2>/dev/null | grep -q '"ok"'; then return 0; fi
    sleep 3
    waited=$((waited + 3))
  done
  return 1
}

# /healthz/deep answers 503 with the same JSON when a check fails, so no -f here. curl's own
# error (refused, timed out) becomes the body, so a message can show why nothing was read.
read_deep() { curl -sS -m 5 "$DEEP_HEALTH_URL" 2>&1 || true; }

# Evaluate one /healthz/deep body (stdin) against the reading taken before the update ($2, may
# be empty). Prints "<state>|<regressed>|<still failing>|<new and failing>": state is ok,
# regressed or unreadable. A check that was ok before regresses when it is not ok now or is
# gone. The worker counts only with a heartbeat at most $1 seconds old, i.e. written after the
# restart: the heartbeat file outlives the old worker. Never fails: bad input is "unreadable".
deep_eval() {
  python3 -c '
import json, sys
def checks(text):
    try:
        found = json.loads(text).get("checks")
    except Exception:
        return None
    return found if isinstance(found, dict) and found else None
def ok(check):
    return isinstance(check, dict) and check.get("ok") is True
try:
    since = float(sys.argv[1])
    before, after = checks(sys.argv[2]) or {}, checks(sys.stdin.read())
    if after is None:
        print("unreadable|||")
        sys.exit()
    regressed = []
    for name, was in before.items():
        if not ok(was):
            continue
        now = after.get(name)
        fine = ok(now)
        if fine and name == "worker":
            try:
                fine = float(now.get("age_seconds")) <= since
            except (TypeError, ValueError):
                fine = False
        if not fine:
            regressed.append(name)
    failing = [n for n, c in after.items() if not ok(c)]
    still = [n for n in failing if n in before]
    new = [n for n in failing if n not in before]
    state = "regressed" if regressed else "ok"
    print("|".join([state, " ".join(regressed), " ".join(still), " ".join(new)]))
except Exception:
    print("unreadable|||")' "$1" "$2" 2>/dev/null || echo "unreadable|||"
}

[ -z "$(git status --porcelain --untracked-files=no)" ] \
  || { say "working tree has local changes; commit or stash them first"; exit 1; }

[ -n "$TARGET" ] || [ "$(git rev-parse --abbrev-ref HEAD)" != "HEAD" ] \
  || { say "detached HEAD (deployed from a tag): pass the tag to deploy, e.g. update.sh v1.2.0"; exit 1; }

# Refuse a commit whose CI run is missing, failed or still running: absent evidence is not a pass.
REQUIRED_CHECKS="test docker e2e"
ci_verdict() {
  curl -fsS -m 15 -H "Accept: application/vnd.github+json" \
    "https://api.github.com/repos/$1/commits/$2/check-runs?per_page=100" \
  | python3 -c '
import json, sys
runs = {r["name"]: r for r in json.load(sys.stdin).get("check_runs", [])}
bad = []
for name in sys.argv[1:]:
    run = runs.get(name)
    if run is None:
        bad.append(name + ": missing")
    elif run["conclusion"] != "success":
        bad.append(name + ": " + (run["conclusion"] or run["status"]))
print("; ".join(bad) or "green")' $REQUIRED_CHECKS
}

git fetch --tags --prune origin
REF="$TARGET"
[ -n "$REF" ] || REF="@{upstream}"
DEPLOY_COMMIT="$(git rev-parse "$REF^{commit}")"
CI_REPO="${CI_REPO:-$(git remote get-url origin | sed -E 's#^.*github\.com[:/]##; s#\.git$##')}"
verdict="$(ci_verdict "$CI_REPO" "$DEPLOY_COMMIT" 2>/dev/null)" || verdict="CI status could not be read"
if [ "$verdict" = "green" ]; then
  say "CI green for $(echo "$DEPLOY_COMMIT" | cut -c1-7) ($REQUIRED_CHECKS)"
elif [ -n "${ALLOW_UNVERIFIED_DEPLOY:-}" ]; then
  say "WARNING: deploying without green CI ($verdict); reason given: $ALLOW_UNVERIFIED_DEPLOY"
else
  say "not deploying $(echo "$DEPLOY_COMMIT" | cut -c1-7): $verdict"
  say "wait for CI, or set ALLOW_UNVERIFIED_DEPLOY=\"<reason>\" to override"
  exit 1
fi

PREVIOUS_COMMIT="$(git rev-parse HEAD)"
PREVIOUS_BRANCH="$(git rev-parse --abbrev-ref HEAD)"
IMAGES_FILE="$ROOT/.update-previous-images"

if [ "${SKIP_BACKUP:-0}" != "1" ]; then
  say "taking a backup first"
  docker compose run --rm --no-deps -e BACKUP_ONCE=1 backup || { say "backup failed, not updating"; exit 1; }
fi

# Remember the image each service runs now (name and id) so a rollback needs no rebuild.
: > "$IMAGES_FILE"
for service in app worker migrate backup; do
  cid="$(docker compose ps -a -q "$service" | head -n 1)"
  [ -n "$cid" ] || continue
  name="$(docker inspect -f '{{.Config.Image}}' "$cid")"
  id="$(docker inspect -f '{{.Image}}' "$cid")"
  # Keep the image reachable under its own tag: with the containerd image store an image that
  # loses its tag to a rebuild can no longer be found by id, so "docker tag $id" fails later.
  docker tag "$id" "$name:rollback" || say "warning: could not keep $name for a rollback"
  echo "$name $id" >> "$IMAGES_FILE"
done

# Read the deep check before touching anything; retry so one rate-limited answer does not
# switch the comparison off.
DEEP_BEFORE=""
for _ in 1 2 3; do
  body="$(read_deep)"
  out="$(printf '%s' "$body" | deep_eval 0 "")"
  if [ "${out%%|*}" != "unreadable" ]; then DEEP_BEFORE="$body"; break; fi
  sleep 3
done
if [ -n "$DEEP_BEFORE" ]; then
  say "deep check before the update read; failing then: [${out##*|}]"
else
  say "no readable deep check before the update ($DEEP_HEALTH_URL): $(printf '%s' "$body" | head -c 200)"
  say "skipping the deep check"
fi

if [ -n "$TARGET" ]; then git checkout --quiet "$TARGET"; else git merge --ff-only "@{upstream}"; fi
say "deploying $(git rev-parse --short HEAD) (was $(echo "$PREVIOUS_COMMIT" | cut -c1-7))"

rollback() {
  say "ROLLING BACK to $(echo "$PREVIOUS_COMMIT" | cut -c1-7)"
  git checkout --quiet "$PREVIOUS_COMMIT"
  # Point the image names back at the previous builds, then start them without building.
  while read -r name id; do docker tag "$name:rollback" "$name" || docker tag "$id" "$name" || true; done < "$IMAGES_FILE"
  docker compose up -d --no-build
  if wait_healthy; then
    say "rollback healthy. The new version is NOT deployed; check: docker compose logs app migrate"
  else
    say "rollback did not become healthy either; check: docker compose logs app migrate"
    say "if the new version ran a migration, the old code cannot start on the newer schema (alembic"
    say "does not know the revision): restore the pre-update backup, see docs/OPERATIONS.md Restore"
  fi
  [ "$PREVIOUS_BRANCH" = "HEAD" ] || git checkout --quiet "$PREVIOUS_BRANCH" 2>/dev/null || true
  exit 1
}

# Wait until every deep check that was ok before is ok again. A regression is reported, not
# rolled back: the new version keeps running and the script exits 2.
check_deep() {
  [ -n "$DEEP_BEFORE" ] || return 0
  deadline=$(( $(date +%s) + HEALTH_TIMEOUT ))
  while :; do
    body="$(read_deep)"
    out="$(printf '%s' "$body" | deep_eval "$(( $(date +%s) - UP_AT ))" "$DEEP_BEFORE")"
    state="${out%%|*}"; rest="${out#*|}"
    regressed="${rest%%|*}"; rest="${rest#*|}"
    still="${rest%%|*}"; new="${rest#*|}"
    [ "$state" = "ok" ] && break
    if [ "$(date +%s)" -ge "$deadline" ]; then
      if [ "$state" = "unreadable" ]; then
        why="deep check unreadable after the update: $(printf '%s' "$body" | head -c 200)"
      else
        why="regressed: $regressed"
      fi
      say "DEPLOYED, BUT $why ($DEEP_HEALTH_URL). Not rolled back: check"
      say "docker compose logs app worker, then fix forward or roll back by hand (docs/OPERATIONS.md)"
      exit 2
    fi
    sleep 3
  done
  [ -z "$still" ] || say "deep check: already failing before the update: $still"
  [ -z "$new" ] || say "deep check: new check failing: $new"
  say "deep check ok for everything that was ok before"
}

if docker compose up -d --build && UP_AT="$(date +%s)" && wait_healthy; then
  say "healthy: $(curl -fsS -m 5 "$HEALTH_URL")"
  # Prune first: a disk regression must not keep the dangling layers (":rollback" tags stay).
  docker image prune -f >/dev/null 2>&1 || true
  check_deep
  exit 0
fi
rollback
