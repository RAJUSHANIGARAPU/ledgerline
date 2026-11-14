#!/bin/sh
# Update a running Ledgerline stack: back up, pull, rebuild, migrate, health check, and roll
# back to the previous code and image when the new version does not become healthy.
#
# Usage (as the deploy user, from anywhere): deploy/hetzner/update.sh [git-ref]
#   git-ref  tag, branch or commit to deploy; default: the current branch, fast-forwarded.
# Environment: HEALTH_URL (default http://127.0.0.1:8000/healthz), HEALTH_TIMEOUT seconds (120),
#              SKIP_BACKUP=1 to skip the pre-update backup.
set -eu

ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
cd "$ROOT"
HEALTH_URL="${HEALTH_URL:-http://127.0.0.1:8000/healthz}"
HEALTH_TIMEOUT="${HEALTH_TIMEOUT:-120}"
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

[ -z "$(git status --porcelain --untracked-files=no)" ] \
  || { say "working tree has local changes; commit or stash them first"; exit 1; }

[ -n "$TARGET" ] || [ "$(git rev-parse --abbrev-ref HEAD)" != "HEAD" ] \
  || { say "detached HEAD (deployed from a tag): pass the tag to deploy, e.g. update.sh v1.2.0"; exit 1; }

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

git fetch --tags --prune origin
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

if docker compose up -d --build && wait_healthy; then
  say "healthy: $(curl -fsS -m 5 "$HEALTH_URL")"
  docker image prune -f >/dev/null 2>&1 || true
  exit 0
fi
rollback
