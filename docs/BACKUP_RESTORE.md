# Backup and restore

## What is backed up

The `backup` service in docker-compose.yml runs `pg_dump -Fc` once a day (default 02:00 UTC, `BACKUP_HOUR_UTC`) and writes `./backups/ledgerline-<UTC timestamp>.dump`. Dumps older than `BACKUP_KEEP_DAYS` (default 30) are deleted. The dump is written to a temporary name first, so a failed run never leaves a truncated file under the real name.

The dump holds the whole database: journal entries, invoices, contacts, bank data, audit events and uploaded attachments (stored in the database). It does not hold `.env`; keep your secrets separately.

Manual backup at any time: `scripts/backup.sh` (writes to `./backups`, or `$BACKUP_DIR`).

## Restore

```sh
scripts/restore.sh backups/ledgerline-20261107T020000Z.dump
```

Restoring on a new server: start the stack once so `ledgerline ensure-app-role` recreates the `ledgerline_app` role and its grants after the restore (restart the app container). The script asks you to type `restore` unless you pass `--yes`. It stops the app, runs `pg_restore --clean --if-exists --no-owner` into the compose database, then starts the app again. Existing data is replaced.

## Off-site copy

A dump next to the database is lost with the machine. Copy `./backups` elsewhere on a schedule, for example with `rsync` or `rclone` to storage you control, and encrypt it if the target is not yours. The dump contains personal data.

## Restore drill (do this once, then each quarter)

A backup you have never restored is a hope, not a backup.

1. Take a fresh dump with `scripts/backup.sh`.
2. On another machine (or with `COMPOSE_PROJECT_NAME=drill` in another directory) start a clean stack: `docker compose up -d --build`.
3. Copy the dump over and run `scripts/restore.sh --yes <file>`.
4. Sign in, open the VAT return and the trial balance for the last closed period, and compare totals with the production system. Open an attachment.
5. Write down the date, the dump name and the result. Tear the drill stack down with `docker compose down -v`.

## Retention of the administration

Fiscal records must be kept for 7 years (10 years for records about real estate), counted from when the data stops being current, according to the Belastingdienst page on keeping records (checked 2026-11-07). Plan backups with that in mind: the 30-day rotation protects against mistakes and loss, it is not your retention archive. Keep a yearly export (Settings, Privacy and data, Export administration) with the dump for as long as the retention period runs. See COMPLIANCE_NL.md for the conflict between this duty and erasure requests.
