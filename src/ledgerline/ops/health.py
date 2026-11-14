"""Checks behind /healthz/deep. Each returns (ok, detail); details carry numbers only."""

import shutil
import time
from collections.abc import Callable
from pathlib import Path

from sqlalchemy import text

from ledgerline.config import Settings
from ledgerline.db import get_engine

WORKER_HEARTBEAT = "worker.heartbeat"
BACKUP_MARKER = "last_backup_ok"

Check = tuple[bool, dict]


def check_database() -> Check:
    try:
        with get_engine().connect() as conn:
            conn.execute(text("SELECT 1"))
        return True, {}
    except Exception:
        return False, {"error": "unreachable"}


def check_worker(settings: Settings, now: float) -> Check:
    path = Path(settings.ops_dir) / WORKER_HEARTBEAT
    try:
        age = now - path.stat().st_mtime
    except OSError:
        return False, {"error": "no heartbeat"}
    return age <= settings.health_worker_max_age_seconds, {"age_seconds": round(age)}


def read_backup_marker(path: Path) -> dict | None:
    """Marker lines: `epoch=<int>`, `offsite=<0|1>`. Anything else is ignored."""
    try:
        pairs = (line.partition("=") for line in path.read_text().splitlines())
        values = {key.strip(): value.strip() for key, _, value in pairs}
        return {"epoch": int(values["epoch"]), "offsite": values.get("offsite") == "1"}
    except (OSError, KeyError, ValueError):
        return None


def check_backup(settings: Settings, now: float) -> Check:
    marker = read_backup_marker(Path(settings.ops_dir) / BACKUP_MARKER)
    if marker is None:
        return False, {"error": "no successful backup recorded"}
    age_hours = (now - marker["epoch"]) / 3600
    ok = age_hours <= settings.health_backup_max_age_hours
    detail = {"age_hours": round(age_hours, 1), "offsite": marker["offsite"]}
    if settings.health_require_offsite_backup and not marker["offsite"]:
        return False, {**detail, "error": "last backup not copied off-site"}
    return ok, detail


def check_disk(settings: Settings) -> Check:
    try:
        usage = shutil.disk_usage(settings.ops_dir)
    except OSError:
        return False, {"error": "cannot read disk usage"}
    free = round(usage.free * 100 / usage.total, 1)
    return free >= settings.health_min_free_disk_percent, {"free_percent": free}


def check_operator_identity(settings: Settings) -> Check:
    """Production must not serve legal pages with placeholders. Names only, never values."""
    if settings.environment != "production":
        return True, {}
    missing = settings.operator_identity.missing()
    return (not missing), ({"missing": missing} if missing else {})


def run_checks(
    settings: Settings,
    now: Callable[[], float] = time.time,
) -> dict:
    moment = now()
    checks = {
        "database": check_database(),
        "worker": check_worker(settings, moment),
        "backup": check_backup(settings, moment),
        "disk": check_disk(settings),
        "legal": check_operator_identity(settings),
    }
    return {
        "status": "ok" if all(ok for ok, _ in checks.values()) else "fail",
        "checks": {name: {"ok": ok, **detail} for name, (ok, detail) in checks.items()},
    }
