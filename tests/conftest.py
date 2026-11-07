import os
import re

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, text
from testcontainers.postgres import PostgresContainer

from ledgerline.models import Role, User

OWNER_PASSWORD = "correct horse battery staple"
_CSRF = re.compile(r'name="csrf_token" value="([^"]+)"')


@pytest.fixture(scope="session")
def database_url():
    with PostgresContainer("postgres:16-alpine", driver="psycopg") as pg:
        url = pg.get_connection_url()
        os.environ["LEDGERLINE_DATABASE_URL"] = url
        os.environ.setdefault("LEDGERLINE_SECRET_KEY", "test-secret-key-not-for-production")
        os.environ["LEDGERLINE_COOKIE_SECURE"] = "false"
        from ledgerline.config import get_settings
        from ledgerline.db import get_engine, get_sessionmaker

        for cached in (get_settings, get_engine, get_sessionmaker):
            cached.cache_clear()
        cfg = Config(os.path.join(os.path.dirname(__file__), "..", "alembic.ini"))
        cfg.set_main_option("sqlalchemy.url", url)
        command.upgrade(cfg, "head")
        yield url


@pytest.fixture
def session(database_url):
    from ledgerline.db import get_sessionmaker

    with get_sessionmaker()() as s:
        yield s
    engine = create_engine(database_url)
    with engine.begin() as conn:
        # Replica mode skips the append-only triggers so tests can start clean.
        conn.execute(text("SET session_replication_role = replica"))
        tables = conn.execute(
            text("SELECT tablename FROM pg_tables WHERE schemaname = 'public' "
                 "AND tablename <> 'alembic_version'")
        ).scalars().all()
        conn.execute(text(f"TRUNCATE {', '.join(tables)} RESTART IDENTITY CASCADE"))
    engine.dispose()


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


def _user(session, email, role):
    from ledgerline.auth import hash_password

    user = User(email=email, password_hash=hash_password(OWNER_PASSWORD), role=role)
    session.add(user)
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
def client(owner, admin):
    return _login(owner.email)


@pytest.fixture
def readonly_client(session, admin, owner):
    accountant = _user(session, "accountant@example.com", Role.ACCOUNTANT)
    return _login(accountant.email)
