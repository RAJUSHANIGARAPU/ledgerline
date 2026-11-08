#!/bin/bash
# Create the firewall, SSH key and server for a Ledgerline pilot with the official hcloud CLI.
# Run it yourself: it creates billable resources. DRY_RUN=1 prints every command instead.
#
# Required: DOMAIN, OWNER_EMAIL, SERVER_TYPE (check the current price: hcloud server-type list)
# Optional: NAME (ledgerline-pilot), LOCATION (fsn1|nbg1|hel1, default fsn1), OWNER_IP (detected
#   when unset), SSH_PUBLIC_KEY_FILE (~/.ssh/id_ed25519.pub), REPO_URL, REPO_REF (main; a tag
#   is better), IMAGE (ubuntu-24.04), HETZNER_BACKUPS=1 (server snapshots, extra monthly cost).
# hcloud authenticates with `hcloud context create` or HCLOUD_TOKEN.
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
NAME="${NAME:-ledgerline-pilot}"
LOCATION="${LOCATION:-fsn1}"
IMAGE="${IMAGE:-ubuntu-24.04}"
SSH_PUBLIC_KEY_FILE="${SSH_PUBLIC_KEY_FILE:-$HOME/.ssh/id_ed25519.pub}"
REPO_URL="${REPO_URL:-https://github.com/RAJUSHANIGARAPU/ledgerline.git}"
REPO_REF="${REPO_REF:-main}"
DRY_RUN="${DRY_RUN:-0}"

die() { echo "error: $*" >&2; exit 1; }
need() { [[ -n "${!1:-}" ]] || die "$1 is required (see the header of $0)"; }
run() { if [[ "$DRY_RUN" == 1 ]]; then printf '+'; printf ' %q' "$@"; echo; else "$@"; fi; }

need DOMAIN; need OWNER_EMAIL; need SERVER_TYPE
[[ "$DOMAIN" =~ ^[A-Za-z0-9]([A-Za-z0-9.-]*[A-Za-z0-9])?\.[A-Za-z]{2,}$ ]] || die "DOMAIN looks invalid"
[[ "$OWNER_EMAIL" =~ ^[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}$ ]] || die "OWNER_EMAIL looks invalid"
[[ "$REPO_REF" =~ ^[A-Za-z0-9._/-]+$ ]] || die "REPO_REF looks invalid"
[[ "$REPO_URL" =~ ^https://[A-Za-z0-9./_-]+$ ]] || die "REPO_URL must be an https URL"
[[ "$NAME" =~ ^[a-z0-9-]+$ ]] || die "NAME must be lowercase letters, digits and dashes"
[[ "$LOCATION" =~ ^(fsn1|nbg1|hel1)$ ]] || die "LOCATION must be fsn1, nbg1 or hel1 (EU locations)"
[[ -r "$SSH_PUBLIC_KEY_FILE" ]] || die "no public key at $SSH_PUBLIC_KEY_FILE"
if [[ "$DRY_RUN" != 1 ]]; then command -v hcloud >/dev/null || die "hcloud CLI not found (https://github.com/hetznercloud/cli)"; fi

if [[ -z "${OWNER_IP:-}" ]]; then
  OWNER_IP="$(curl -fsS -m 10 https://api.ipify.org)" || die "set OWNER_IP (your public IPv4)"
  echo "SSH will be allowed from $OWNER_IP only (detected). Set OWNER_IP to override."
fi
[[ "$OWNER_IP" =~ ^[0-9]{1,3}(\.[0-9]{1,3}){3}$ ]] || die "OWNER_IP must be an IPv4 address"

PUBLIC_KEY="$(tr -d '\n' < "$SSH_PUBLIC_KEY_FILE")"
[[ "$PUBLIC_KEY" =~ ^(ssh-ed25519|ssh-rsa|ecdsa-sha2-[a-z0-9]+)\ [A-Za-z0-9+/=]+(\ [^\"\|\&]*)?$ ]] || die "public key format not recognised"

USER_DATA="$(mktemp)"
trap 'rm -f "$USER_DATA"' EXIT
sed -e "s|@@DOMAIN@@|$DOMAIN|g" -e "s|@@OWNER_EMAIL@@|$OWNER_EMAIL|g" \
    -e "s|@@REPO_URL@@|$REPO_URL|g" -e "s|@@REPO_REF@@|$REPO_REF|g" \
    -e "s|@@SSH_PUBLIC_KEY@@|$PUBLIC_KEY|g" "$HERE/cloud-init.yaml" > "$USER_DATA"
if grep -q '@@[A-Z_]*@@' "$USER_DATA"; then die "unfilled placeholder in cloud-init"; fi

run hcloud ssh-key create --name "$NAME-key" --public-key-from-file "$SSH_PUBLIC_KEY_FILE"
run hcloud firewall create --name "$NAME-fw"
run hcloud firewall add-rule "$NAME-fw" --direction in --protocol tcp --port 22 \
  --source-ips "$OWNER_IP/32" --description ssh-owner
run hcloud firewall add-rule "$NAME-fw" --direction in --protocol tcp --port 80 \
  --source-ips 0.0.0.0/0 --source-ips ::/0 --description http
run hcloud firewall add-rule "$NAME-fw" --direction in --protocol tcp --port 443 \
  --source-ips 0.0.0.0/0 --source-ips ::/0 --description https

create=(hcloud server create --name "$NAME" --type "$SERVER_TYPE" --image "$IMAGE"
  --location "$LOCATION" --ssh-key "$NAME-key" --firewall "$NAME-fw"
  --user-data-from-file "$USER_DATA")
[[ "${HETZNER_BACKUPS:-0}" == 1 ]] && create+=(--enable-backup)
run "${create[@]}"

cat <<MSG

Server requested. Next steps:
  1. Point an A record for $DOMAIN at the server IP (hcloud server ip $NAME).
  2. Wait about 5 minutes, then: ssh deploy@<ip>   (bootstrap log: /var/log/ledgerline-bootstrap.log)
  3. Read ~/FIRST-LOGIN.txt once and delete it.
  4. Fill in RESTIC_*, AWS_* and the ping URLs in ~/ledgerline/.env, then: docker compose up -d
  5. Follow docs/OPERATIONS.md (restore drill before you trust the backups).
MSG
