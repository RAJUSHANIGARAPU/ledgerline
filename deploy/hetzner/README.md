# Hetzner Cloud pilot server

One server runs everything: Caddy (HTTPS) in front of the compose stack (db, migrate, app, worker, backup). You run the scripts; nothing here is created for you and nothing is paid until you run `provision.sh`.

## Before you start

- A Hetzner Cloud account and a project; an API token (read and write) for the `hcloud` CLI: `hcloud context create ledgerline`.
- The `hcloud` CLI (MIT, github.com/hetznercloud/cli).
- A domain you control (an A record will point at the server).
- An off-site backup target: S3-compatible storage (Hetzner Object Storage or any other) and a password manager entry for the restic passphrase.
- Prices change: check the current price of the server type and of Hetzner's server backups (an optional extra, a share of the server price) in the Hetzner console or with `hcloud server-type list` before you run the script.

## Provision

```sh
export DOMAIN=books.example.com OWNER_EMAIL=you@example.com
export SERVER_TYPE=<type from: hcloud server-type list>
export LOCATION=fsn1                 # fsn1, nbg1 or hel1 (EU)
export REPO_REF=v1.0.0               # a tag is better than main
# export HETZNER_BACKUPS=1           # optional Hetzner server backups (extra cost)
DRY_RUN=1 deploy/hetzner/provision.sh   # prints the commands
deploy/hetzner/provision.sh
```

It creates an SSH key, a firewall (22 from your current IPv4 only, found with api.ipify.org unless you set `OWNER_IP`; 80 and 443 from anywhere) and the server with `cloud-init.yaml` as user data. The user data contains no secrets.

If your home IP changes, update the firewall rule: `hcloud firewall describe ledgerline-pilot-fw`, then `delete-rule` and `add-rule`.

## What cloud-init does (about 5 minutes)

- Installs Docker from Docker's apt repository, Caddy from Caddy's repository, unattended-upgrades and fail2ban.
- Creates the `deploy` user (key login only, no password, in the `docker` group); root login and password login over SSH are disabled.
- Clones the repository at `REPO_REF`, writes `.env` from `env.template` with passwords generated on the server (mode 600), writes `/etc/caddy/Caddyfile` from `Caddyfile.template` (12 MB request body limit) and starts the stack.
- Writes the first owner password to `~deploy/FIRST-LOGIN.txt`.
- Log: `/var/log/ledgerline-bootstrap.log`.

## After the first boot

1. DNS: A record for your domain to `hcloud server ip ledgerline-pilot`.
2. `ssh deploy@<ip>`; read and delete `FIRST-LOGIN.txt`.
3. Edit `~/ledgerline/.env`: the four `LEDGERLINE_OPERATOR_*` values (legal name, KvK number, address, contact email; `/healthz/deep` returns 503 until they are set), the `RESTIC_*`, `AWS_*` and ping settings (marked "Fill in"). `docker compose up -d`.
4. `curl -s https://<domain>/healthz/deep`: it stays 503 until the first off-site backup has run. The backup service runs one at start, and again at start when a repository was added after a local-only run, so it clears within a few minutes.
5. Run `scripts/restore-drill.sh` and keep the passphrase and storage key outside the server. Then continue with docs/OPERATIONS.md (monitor, pings, monthly drill).

## Update

`deploy/hetzner/update.sh <tag>` (backup, deploy, health check, automatic rollback). See docs/OPERATIONS.md.

## Remove everything

`hcloud server delete ledgerline-pilot`, `hcloud firewall delete ledgerline-pilot-fw`, `hcloud ssh-key delete ledgerline-pilot-key`. Delete the off-site bucket separately; it holds client data.

## Not verified here

The scripts were checked with shellcheck and `DRY_RUN=1`, and the YAML parses; the `hcloud` flags were read from the CLI's reference documentation (v1.70.2). A real server was not created, so cloud-init and the Caddy certificate step have not run end to end. Do the first run with a throw-away DNS name and delete the server afterwards if anything looks off.
