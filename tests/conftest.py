import os
import re
from contextlib import contextmanager
from dataclasses import dataclass

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url
from testcontainers.postgres import PostgresContainer

from ledgerline.models import Membership, Role, User

OWNER_PASSWORD = "correct horse battery staple"
_CSRF = re.compile(r'name="csrf_token" value="([^"]+)"')


APP_ROLE_PASSWORD = "test-app-role-password"


@dataclass(frozen=True)
class TenantRef:
    id: int
    name: str


@pytest.fixture(scope="session")
def owner_url():
    with PostgresContainer("postgres:16-alpine", driver="psycopg") as pg:
        url = pg.get_connection_url()
        cfg = Config(os.path.join(os.path.dirname(__file__), "..", "alembic.ini"))
        cfg.set_main_option("sqlalchemy.url", url)
        command.upgrade(cfg, "head")
        engine = create_engine(url)
        with engine.begin() as conn:
            conn.execute(text(f"ALTER ROLE ledgerline_app LOGIN PASSWORD '{APP_ROLE_PASSWORD}'"))
        engine.dispose()
        yield url


@pytest.fixture(scope="session")
def database_url(owner_url):
    """Connection URL of the restricted application role; every app fixture uses it."""
    url = make_url(owner_url).set(username="ledgerline_app", password=APP_ROLE_PASSWORD)
    app_url = url.render_as_string(hide_password=False)
    os.environ["LEDGERLINE_DATABASE_URL"] = app_url
    os.environ["LEDGERLINE_MIGRATION_DATABASE_URL"] = owner_url
    os.environ.setdefault("LEDGERLINE_SECRET_KEY", "test-secret-key-not-for-production")
    os.environ["LEDGERLINE_COOKIE_SECURE"] = "false"
    os.environ["LEDGERLINE_ENVIRONMENT"] = "development"
    from ledgerline.config import get_settings
    from ledgerline.db import get_engine, get_sessionmaker

    for cached in (get_settings, get_engine, get_sessionmaker):
        cached.cache_clear()
    return app_url


@pytest.fixture(scope="session")
def owner_engine(owner_url):
    engine = create_engine(owner_url)
    yield engine
    engine.dispose()


def _make_tenant(owner_engine, name: str) -> TenantRef:
    with owner_engine.begin() as conn:
        tenant_id = conn.execute(
            text("INSERT INTO tenants (name) VALUES (:n) RETURNING id"), {"n": name}
        ).scalar_one()
    return TenantRef(tenant_id, name)


@pytest.fixture
def tenant(database_url, owner_engine):
    ref = _make_tenant(owner_engine, "Tenant A")
    yield ref
    with owner_engine.begin() as conn:
        # Replica mode skips the append-only triggers so tests can start clean.
        conn.execute(text("SET session_replication_role = replica"))
        tables = conn.execute(
            text("SELECT tablename FROM pg_tables WHERE schemaname = 'public' "
                 "AND tablename <> 'alembic_version'")
        ).scalars().all()
        conn.execute(text(f"TRUNCATE {', '.join(tables)} RESTART IDENTITY CASCADE"))


@pytest.fixture
def session(tenant):
    from ledgerline.db import get_sessionmaker, set_tenant

    with get_sessionmaker()() as s:
        set_tenant(s, tenant.id)
        yield s


@contextmanager
def tenant_db(tenant_id: int):
    from ledgerline.db import tenant_session

    with tenant_session(tenant_id) as s:
        yield s


@pytest.fixture
def admin(session):
    from ledgerline.ledger.chart import create_administration

    administration = create_administration(
        session,
        name="Testbedrijf",
        kvk_number="12345678",
        vat_number="NL123456789B01",
        iban="NL91ABNA0417164300",
        address_line="Teststraat 1",
        postcode="1011 AB",
        city="Amsterdam",
        email="info@testbedrijf.example",
    )
    session.commit()
    return administration


def _user(session, email, role, tenant_id=None):
    from ledgerline.auth import hash_password

    user = User(email=email, password_hash=hash_password(OWNER_PASSWORD))
    session.add(user)
    session.flush()
    session.add(Membership(user_id=user.id, tenant_id=tenant_id or session.info["tenant_id"],
                           role=role))
    session.commit()
    return user


@pytest.fixture
def owner(session):
    return _user(session, "owner@example.com", Role.OWNER)


def _login(email):
    from fastapi.testclient import TestClient

    from ledgerline.main import create_app

    client = TestClient(create_app())
    token = _CSRF.search(client.get("/login").text).group(1)
    response = client.post(
        "/login",
        data={"email": email, "password": OWNER_PASSWORD, "csrf_token": token},
        follow_redirects=False,
    )
    assert response.status_code == 303, response.text
    client.csrf = _CSRF.search(client.get("/login").text).group(1)
    return client


@pytest.fixture
def bookkeeper(session):
    return _user(session, "bookkeeper@example.com", Role.BOOKKEEPER)


@pytest.fixture
def bookkeeper_client(bookkeeper, admin):
    return _login(bookkeeper.email)


@pytest.fixture
def other_tenant(tenant, owner_engine):
    return _make_tenant(owner_engine, "Tenant B")


@pytest.fixture
def other_admin(other_tenant):
    from ledgerline.ledger.chart import create_administration

    with tenant_db(other_tenant.id) as s:
        administration = create_administration(s, name="Andere Zaak", kvk_number="87654321")
        s.commit()
        return administration


@pytest.fixture
def other_owner(other_tenant, other_admin):
    with tenant_db(other_tenant.id) as s:
        return _user(s, "owner-b@example.com", Role.OWNER)


@pytest.fixture
def client(owner, admin):
    return _login(owner.email)


@pytest.fixture
def readonly_client(session, admin, owner):
    accountant = _user(session, "accountant@example.com", Role.ACCOUNTANT)
    return _login(accountant.email)


@pytest.fixture
def uploader(session, admin, owner):
    return _user(session, "uploader@example.com", Role.UPLOADER)


@pytest.fixture
def uploader_client(uploader):
    return _login(uploader.email)


@pytest.fixture(autouse=True)
def _fresh_token_limits():
    from ledgerline.accounts import ratelimit
    from ledgerline.mobile import tokens

    tokens.reset_rate_limits()
    ratelimit.reset_all()
    yield
    tokens.reset_rate_limits()
    ratelimit.reset_all()
