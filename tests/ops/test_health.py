import os
import time
from types import SimpleNamespace

import pytest
from sqlalchemy import create_engine

from ledgerline.ops import health, routes


def beat(path, age=0):
    path.touch()
    stamp = time.time() - age
    os.utime(path, (stamp, stamp))


def marker(path, age_hours=1.0, offsite=1):
    path.write_text(f"epoch={int(time.time() - age_hours * 3600)}\noffsite={offsite}\n")


@pytest.fixture
def healthy(ops_dir, monkeypatch):
    beat(ops_dir / "worker.heartbeat", 3)
    marker(ops_dir / "last_backup_ok")
    # A fixed half-empty disk, so the result does not depend on the machine running the tests.
    monkeypatch.setattr(
        health.shutil, "disk_usage", lambda _p: SimpleNamespace(total=1000, used=500, free=500)
    )
    return ops_dir


def get(client):
    response = client.get("/healthz/deep")
    return response, response.json()


def test_all_checks_pass(deep_client, healthy):
    response, body = get(deep_client)
    assert response.status_code == 200
    assert body["status"] == "ok"
    assert set(body["checks"]) == {"database", "worker", "backup", "disk"}
    assert all(check["ok"] for check in body["checks"].values())
    assert body["checks"]["worker"]["age_seconds"] in range(2, 10)
    assert body["checks"]["backup"]["offsite"] is True
    assert response.headers["cache-control"] == "no-store"


def test_stale_worker_heartbeat_is_503(deep_client, healthy):
    beat(healthy / "worker.heartbeat", 400)
    response, body = get(deep_client)
    assert response.status_code == 503
    assert body["status"] == "fail"
    assert body["checks"]["worker"]["ok"] is False
    assert body["checks"]["worker"]["age_seconds"] >= 400
    assert body["checks"]["database"]["ok"] is True


def test_missing_heartbeat_is_503(deep_client, healthy):
    (healthy / "worker.heartbeat").unlink()
    response, body = get(deep_client)
    assert response.status_code == 503
    assert body["checks"]["worker"] == {"ok": False, "error": "no heartbeat"}


def test_old_backup_is_503(deep_client, healthy):
    marker(healthy / "last_backup_ok", age_hours=27)
    response, body = get(deep_client)
    assert response.status_code == 503
    assert body["checks"]["backup"]["ok"] is False
    assert body["checks"]["backup"]["age_hours"] == pytest.approx(27, abs=0.1)


def test_backup_just_inside_the_limit_passes(deep_client, healthy):
    marker(healthy / "last_backup_ok", age_hours=25.5)
    assert get(deep_client)[0].status_code == 200


def test_no_backup_marker_is_503(deep_client, healthy):
    (healthy / "last_backup_ok").unlink()
    response, body = get(deep_client)
    assert response.status_code == 503
    assert body["checks"]["backup"]["error"] == "no successful backup recorded"


def test_garbled_backup_marker_is_503(deep_client, healthy):
    (healthy / "last_backup_ok").write_text("epoch=soon\n")
    assert get(deep_client)[0].status_code == 503


def test_offsite_required_but_missing(deep_client, healthy, monkeypatch):
    from ledgerline.config import get_settings

    marker(healthy / "last_backup_ok", offsite=0)
    assert get(deep_client)[0].status_code == 200
    monkeypatch.setenv("LEDGERLINE_HEALTH_REQUIRE_OFFSITE_BACKUP", "true")
    get_settings.cache_clear()
    response, body = get(deep_client)
    assert response.status_code == 503
    assert body["checks"]["backup"]["error"] == "last backup not copied off-site"


def test_low_disk_is_503(deep_client, healthy, monkeypatch):
    monkeypatch.setattr(
        health.shutil, "disk_usage", lambda _p: SimpleNamespace(total=1000, used=950, free=50)
    )
    response, body = get(deep_client)
    assert response.status_code == 503
    assert body["checks"]["disk"] == {"ok": False, "free_percent": 5.0}


def test_database_down_is_503_without_leaking_the_error(deep_client, healthy, monkeypatch):
    dead = create_engine(
        "postgresql+psycopg://x:secret-pw@127.0.0.1:1/none", connect_args={"connect_timeout": 1}
    )
    monkeypatch.setattr(health, "get_engine", lambda: dead)
    response, body = get(deep_client)
    assert response.status_code == 503
    assert body["checks"]["database"] == {"ok": False, "error": "unreachable"}
    assert "secret-pw" not in response.text
    assert body["checks"]["worker"]["ok"] is True


def test_every_failure_is_reported_at_once(deep_client, ops_dir, monkeypatch):
    monkeypatch.setattr(
        health.shutil, "disk_usage", lambda _p: SimpleNamespace(total=1000, used=500, free=500)
    )
    response, body = get(deep_client)
    assert response.status_code == 503
    failing = {name for name, check in body["checks"].items() if not check["ok"]}
    assert failing == {"worker", "backup"}


def test_response_carries_no_tenant_or_secret_fields(deep_client, healthy):
    text = get(deep_client)[0].text
    for word in ("postgresql", "password", "tenant", "email", "ledgerline_app"):
        assert word not in text.lower()


def test_rate_limit_returns_429_after_the_quota(deep_client, healthy, monkeypatch):
    monkeypatch.setattr(routes, "_limiter", routes.RateLimiter(3))
    codes = [deep_client.get("/healthz/deep").status_code for _ in range(5)]
    assert codes == [200, 200, 200, 429, 429]
    assert deep_client.get("/healthz/deep").headers["retry-after"] == "60"


def test_rate_limiter_window_slides():
    limiter = routes.RateLimiter(2)
    assert limiter.allow("a", now=0) and limiter.allow("a", now=1)
    assert not limiter.allow("a", now=2)
    assert limiter.allow("b", now=2)
    assert limiter.allow("a", now=61)


def test_plain_healthz_is_unchanged(deep_client):
    assert deep_client.get("/healthz").json() == {"status": "ok"}


def settings_with(**env):
    from ledgerline.config import Settings

    return Settings(_env_file=None, secret_key="test", **env)


def test_disk_threshold_defaults_to_ten_percent():
    assert settings_with().health_min_free_disk_percent == 10


def test_disk_threshold_is_read_from_the_environment(monkeypatch):
    monkeypatch.setenv("LEDGERLINE_HEALTH_MIN_FREE_DISK_PERCENT", "5")
    assert settings_with().health_min_free_disk_percent == 5


@pytest.mark.parametrize("value", ["0", "-1", "100", "250", "ten", "", "7.5"])
def test_disk_threshold_rejects_nonsense(monkeypatch, value):
    from pydantic import ValidationError

    monkeypatch.setenv("LEDGERLINE_HEALTH_MIN_FREE_DISK_PERCENT", value)
    with pytest.raises(ValidationError):
        settings_with()
