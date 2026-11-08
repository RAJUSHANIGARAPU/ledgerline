import re
from datetime import UTC, datetime, timedelta

import pyotp
import pytest
from sqlalchemy import text

from tests.conftest import OWNER_PASSWORD, _login

_LINK = re.compile(r"https?://[^\s\"<]+/(?:invite|reset)/[\w-]+")
FIXED = datetime(2026, 11, 8, 12, 0, 0, tzinfo=UTC)


class FakeSMTP:
    instances: list["FakeSMTP"] = []
    messages: list = []

    def __init__(self, host, port, timeout=None, context=None):
        self.host, self.port, self.context = host, port, context
        self.calls = []
        FakeSMTP.instances.append(self)

    def __enter__(self):
        return self

    def __exit__(self, *_):
        return False

    def ehlo(self):
        self.calls.append("ehlo")

    def starttls(self, context=None):
        self.calls.append("starttls")

    def login(self, user, password):
        self.calls.append(("login", user))

    def send_message(self, message):
        self.calls.append("send")
        FakeSMTP.messages.append(message)


class FakeSMTPSSL(FakeSMTP):
    pass


def body(message) -> str:
    return message.get_body(("plain",)).get_content()


def link_in(message) -> str:
    return _LINK.search(body(message)).group(0)


def path_of(link: str) -> str:
    return "/" + link.split("/", 3)[3]


@pytest.fixture(autouse=True)
def _settings(database_url):
    from ledgerline.config import get_settings

    yield
    get_settings.cache_clear()


@pytest.fixture
def smtp_env(monkeypatch):
    from ledgerline.config import get_settings

    for key, value in {"SMTP_HOST": "smtp.test", "SMTP_FROM": "Ledgerline <noreply@test.example>",
                       "SMTP_USER": "mailer", "SMTP_PASSWORD": "mail-secret",
                       "ENVIRONMENT": "production"}.items():
        monkeypatch.setenv(f"LEDGERLINE_{key}", value)
    get_settings.cache_clear()


@pytest.fixture
def outbox(smtp_env, monkeypatch):
    from ledgerline import mail

    FakeSMTP.instances.clear()
    FakeSMTP.messages.clear()
    monkeypatch.setattr(mail.smtplib, "SMTP", FakeSMTP)
    monkeypatch.setattr(mail.smtplib, "SMTP_SSL", FakeSMTPSSL)
    return FakeSMTP.messages


class Clock:
    def __init__(self):
        self.t = FIXED

    def advance(self, **kwargs):
        self.t += timedelta(**kwargs)


@pytest.fixture
def clock(monkeypatch):
    from ledgerline.accounts import clock as clock_module

    c = Clock()
    monkeypatch.setattr(clock_module, "now", lambda: c.t)
    return c


@pytest.fixture
def q(owner_engine):
    def run(sql, **params):
        with owner_engine.connect() as conn:
            return conn.execute(text(sql), params).all()

    return run


def login_page_client(email="owner@example.com", password=OWNER_PASSWORD):
    return _login(email)


def totp_code(secret: str, at: datetime) -> str:
    return pyotp.TOTP(secret).at(at)


def read_secret(client) -> str:
    html = client.get("/account/security").text
    return re.search(r'id="totp-secret">([A-Z2-7]+)<', html).group(1)


def enable_2fa(client, clock: Clock) -> tuple[str, list[str]]:
    client.post("/account/security/2fa/start", data={"csrf_token": client.csrf})
    secret = read_secret(client)
    r = client.post("/account/security/2fa/enable",
                    data={"code": totp_code(secret, clock.t), "csrf_token": client.csrf})
    assert r.status_code == 200 and "recovery-codes" in r.text, r.text[:300]
    codes = re.findall(r"<code>([a-z0-9-]{19})</code>", r.text)
    return secret, codes


def password_login(client_factory_email, password=OWNER_PASSWORD):
    from fastapi.testclient import TestClient

    from ledgerline.main import create_app

    client = TestClient(create_app())
    token = re.search(r'name="csrf_token" value="([^"]+)"', client.get("/login").text).group(1)
    response = client.post("/login", data={"email": client_factory_email, "password": password,
                                           "csrf_token": token}, follow_redirects=False)
    page = client.get("/login").text
    client.csrf = re.search(r'name="csrf_token" value="([^"]+)"', page).group(1)
    return client, response


def submit_code(client, code):
    token = re.search(r'name="csrf_token" value="([^"]+)"', client.get("/login/totp").text).group(1)
    return client.post("/login/totp", data={"code": code, "csrf_token": token},
                       follow_redirects=False)
