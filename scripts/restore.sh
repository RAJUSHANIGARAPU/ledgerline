#!/bin/sh
# Restore a dump into the compose database. Replaces ALL existing data in that project.
# Usage: scripts/restore.sh --project <compose project> [--yes] <dump file>
#   --project  the compose project to restore into (or LEDGERLINE_RESTORE_PROJECT). Required.
#              It must be the project this directory's compose file resolves to, so a restore
#              run from the wrong checkout or shell is refused before anything is touched.
#   --yes      skip the typed confirmation (CI, scripted drills); --project is still required.
# Without --yes you are asked to type the project name.
set -eu

usage() { echo "usage: restore.sh --project <compose project> [--yes] <dump file>" >&2; exit 2; }

project="${LEDGERLINE_RESTORE_PROJECT:-}"
yes=0
while [ $# -gt 0 ]; do
  case "$1" in
    --project) [ $# -ge 2 ] || usage; project="$2"; shift 2 ;;
    --yes) yes=1; shift ;;
    -h|--help) sed -n '2,9p' "$0"; exit 0 ;;
    -*) echo "unknown argument: $1" >&2; usage ;;
    *) break ;;
  esac
done
[ -n "$project" ] || { echo "refusing: no target project. Pass --project <name> (or set LEDGERLINE_RESTORE_PROJECT)." >&2; exit 1; }
[ $# -eq 1 ] || usage
file="$1"
[ -f "$file" ] || { echo "no such file: $file" >&2; exit 1; }

# The project plain `docker compose` would act on from here (COMPOSE_PROJECT_NAME, else the directory).
actual="$(docker compose config 2>/dev/null | sed -n 's/^name: //p' | head -n 1)"
[ -n "$actual" ] || { echo "refusing: cannot resolve the compose project from $(pwd)" >&2; exit 1; }
if [ "$actual" != "$project" ]; then
  echo "refusing: --project is '$project' but this directory's compose project is '$actual'. Nothing was changed." >&2
  exit 1
fi

if [ "$yes" -ne 1 ]; then
  printf 'This replaces ALL data in the ledgerline database of compose project "%s" with %s.\nType the project name to continue: ' "$project" "$file"
  read -r answer
  [ "$answer" = "$project" ] || { echo "aborted"; exit 1; }
fi

dc() { docker compose -p "$project" "$@"; }
dc stop app
dc exec -T db pg_restore -U ledgerline -d ledgerline --clean --if-exists --no-owner < "$file"
dc start app
echo "restored from $file into project $project"
