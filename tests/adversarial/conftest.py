"""Fixtures for the adversarial tenancy tests (reuse the implementer's seeding helpers)."""

import base64
import json

import pytest
from itsdangerous import TimestampSigner

from tests.tenancy.conftest import app_connection, seed_tenant, seeded_a, seeded_b  # noqa: F401


def use_admin(client, administration_id: int) -> None:
    r = client.post(
        "/administration/switch",
        data={"administration_id": administration_id, "csrf_token": client.csrf},
        follow_redirects=False,
    )
    assert r.status_code == 303, r.status_code


def forge_session(client, **values) -> None:
    """Sign a session cookie with the test secret: models a stolen secret key or a stale cookie."""
    from ledgerline.config import get_settings

    values.setdefault("sv", 1)
    signer = TimestampSigner(get_settings().secret_key)
    raw = base64.b64encode(json.dumps(values).encode())
    client.cookies.clear()
    client.cookies.set("ledgerline_session", signer.sign(raw).decode())


@pytest.fixture
def q(owner_engine):
    """Run a read query as the table owner (bypasses RLS) and return rows."""
    from sqlalchemy import text

    def run(sql, **params):
        with owner_engine.connect() as conn:
            return conn.execute(text(sql), params).all()

    return run
