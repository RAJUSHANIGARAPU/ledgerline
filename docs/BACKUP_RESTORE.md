# Backup and restore

The full runbook is OPERATIONS.md; this page describes what a backup contains and how to restore it.

## What is backed up

The `backup` service in docker-compose.yml runs once a day (default 02:00 UTC, `BACKUP_HOUR_UTC`), and once at start when the last success is older than 24 hours. Each run writes, from one database snapshot:

- `./backups/ledgerline-<UTC timestamp>.dump`: `pg_dump -Fc` as the owner role. The dump is written under a temporary name first, so a failed run never leaves a truncated file. Dumps older than `BACKUP_KEEP_DAYS` (default 30) are deleted. This local tier is for fast recovery from a mistake.
- `./backups/ledgerline-<UTC timestamp>.manifest.txt`: row count of every table at that moment, the schema revision and the SHA-256 of the dump. The restore drill checks a restore against it.
- When `RESTIC_REPOSITORY` and `RESTIC_PASSWORD` are set: an encrypted copy of both files in a restic repository on S3-compatible storage, with retention 14 daily, 8 weekly, 24 monthly and 8 yearly snapshots, and a weekly `restic check`. Without these settings only the local tier exists, and `/healthz/deep` can be told to treat that as unhealthy (`LEDGERLINE_HEALTH_REQUIRE_OFFSITE_BACKUP=true`).

The dump holds the whole database: journal entries, invoices, contacts, bank data, audit events and uploaded attachments (stored in the database), for every tenant. It does not hold `.env`; keep your secrets separately, and keep `RESTIC_PASSWORD` outside the server: without it the off-site copy cannot be read.

Manual backup at any time: `scripts/backup.sh` (local dump only, to `./backups` or `$BACKUP_DIR`), or `docker compose run --rm -e BACKUP_ONCE=1 backup` for the full run including the off-site copy.

## Restore

```sh
scripts/restore.sh backups/ledgerline-20261107T020000Z.dump
```

Restoring on a new server: start the stack once so `ledgerline ensure-app-role` recreates the `ledgerline_app` role and its grants after the restore (restart the app container). The script asks you to type `restore` unless you pass `--yes`. It stops the app, runs `pg_restore --clean --if-exists --no-owner` into the compose database, then starts the app again. Existing data is replaced.

To fetch a dump from the off-site repository, see "Restore" in OPERATIONS.md.

## Restore drill (monthly)

A backup you have never restored is a hope, not a backup. `scripts/restore-drill.sh` does the whole drill on any machine with Docker: it restores the latest (or a given) off-site snapshot into a throw-away compose project, checks every table's row count against the manifest, runs the migrations, starts the app, requests `/healthz` and prints `RESULT: PASS` or `RESULT: FAIL - <reason>`. Details and options are in OPERATIONS.md. Also open the VAT return and the trial balance of the last closed period in a kept drill (`--keep`) and compare totals with production once per quarter.

## Retention of the administration

Fiscal records must be kept for 7 years (10 years for records about real estate), counted from when the data stops being current, according to the Belastingdienst page on keeping records (checked 2026-11-07). The off-site repository keeps a snapshot per year for 8 years (`keep-yearly 8`), which covers disaster recovery over that period but is not the legal archive: that is each client's own administration. Ask every client to keep a yearly export (Settings, Privacy and data, Export administration) for as long as the retention period runs. See COMPLIANCE_NL.md for the conflict between this duty and erasure requests, which cannot reach old snapshots.
