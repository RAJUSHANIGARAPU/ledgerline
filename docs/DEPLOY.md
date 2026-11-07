# Deploying Ledgerline

Ledgerline runs as three containers: the app, PostgreSQL 16 and a nightly backup job. Everything is free software; no paid service is needed.

## Local, one command

Requires Docker with the Compose plugin.

```sh
cp .env.example .env
# edit .env: set LEDGERLINE_SECRET_KEY and POSTGRES_PASSWORD
# for plain http://localhost also set LEDGERLINE_COOKIE_SECURE=false
docker compose up -d --build
```

Open http://127.0.0.1:8000. The app refuses to start without `LEDGERLINE_SECRET_KEY`, and compose refuses to start without both secrets.

Create the first owner, either by setting `LEDGERLINE_INITIAL_OWNER_EMAIL` and `LEDGERLINE_INITIAL_PASSWORD` in `.env` before the first start, or afterwards:

```sh
docker compose exec -e LEDGERLINE_INITIAL_PASSWORD='a-long-passphrase' app \
  ledgerline create-user --email you@example.com --role owner
docker compose exec app ledgerline create-administration --name "My Company" --legal-form eenmanszaak
```

Remove `LEDGERLINE_INITIAL_PASSWORD` from `.env` once the owner exists.

`LEDGERLINE_COOKIE_SECURE=false` is for local http only. With it on (the default), browsers drop the session cookie over plain http and you cannot sign in.

Migrations run automatically on every start (`ledgerline migrate`).

## A VPS with HTTPS (Caddy)

1. Any Linux VPS with Docker. Point a DNS A record (for example `books.example.com`) at it.
2. Clone the repository, create `.env` as above and keep `LEDGERLINE_COOKIE_SECURE=true`.
3. Leave `LEDGERLINE_BIND` at its default `127.0.0.1:8000` so the app is reachable only through the proxy.
4. `docker compose up -d --build`.
5. Install Caddy from its official packages and use this `/etc/caddy/Caddyfile`:

```
books.example.com {
    encode zstd gzip
    reverse_proxy 127.0.0.1:8000
}
```

Caddy obtains and renews a Let's Encrypt certificate by itself. The app starts uvicorn with `--proxy-headers`, so the original scheme and client address are taken from the proxy. Open only ports 80 and 443 in the firewall (plus SSH).

## Updating

```sh
git pull
docker compose up -d --build
```

Take a backup first (`scripts/backup.sh`). Migrations are applied on start.

## Operations notes

- Logs: `docker compose logs -f app`.
- Health: `GET /healthz` (also the container HEALTHCHECK).
- Data lives in the `pgdata` volume. Attachments are stored in the database, so one dump contains everything.
- Backups: see BACKUP_RESTORE.md. A backup stored on the same machine is not a disaster backup; copy `./backups` off the host.
- Hosting provider: if a third party hosts the server, a data processing agreement is needed (see COMPLIANCE_NL.md).
