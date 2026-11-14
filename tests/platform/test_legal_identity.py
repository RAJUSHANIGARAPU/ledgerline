"""Operator identity on the legal pages, and the production health check that guards it."""
import pytest

from ledgerline.config import get_settings
from ledgerline.ops import health, routes

IDENTITY = {
    "OPERATOR_LEGAL_NAME": "Voorbeeld Boekhoudsoftware B.V.",
    "OPERATOR_KVK_NUMBER": "00000000",
    "OPERATOR_ADDRESS": "Voorbeeldstraat 1, 1000 AA Voorbeeldstad",
    "OPERATOR_CONTACT_EMAIL": "privacy@voorbeeld.example",
}


@pytest.fixture
def identity(monkeypatch, database_url):
    for key, value in IDENTITY.items():
        monkeypatch.setenv(f"LEDGERLINE_{key}", value)
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


@pytest.fixture
def anon(database_url):
    from fastapi.testclient import TestClient

    from ledgerline.main import create_app

    return TestClient(create_app())


def test_privacy_and_terms_render_the_configured_identity(identity, anon):
    privacy = anon.get("/legal/privacy").text
    terms = anon.get("/legal/terms").text
    for value in IDENTITY.values():
        assert value in privacy or value in terms
    for needle in ("Voorbeeld Boekhoudsoftware B.V.", "00000000", "Voorbeeldstraat 1",
                   "privacy@voorbeeld.example"):
        assert needle in privacy
    assert "Voorbeeld Boekhoudsoftware B.V." in terms
    for placeholder in ("[CONTROLLER NAME]", "[ADDRESS]", "[KVK NUMBER]",
                        "[PRIVACY CONTACT EMAIL]", "[OPERATOR NAME]", "[CONTACT EMAIL]"):
        assert placeholder not in privacy + terms


def test_identity_is_html_escaped(monkeypatch, database_url, anon):
    monkeypatch.setenv("LEDGERLINE_OPERATOR_LEGAL_NAME", "<script>alert(1)</script> B.V.")
    get_settings.cache_clear()
    try:
        assert "<script>alert(1)</script>" not in anon.get("/legal/terms").text
    finally:
        get_settings.cache_clear()


def test_unset_identity_still_shows_a_visible_placeholder(anon):
    assert "[PRIVACY CONTACT EMAIL]" in anon.get("/legal/privacy").text


def deep(monkeypatch, tmp_path, environment, **env):
    from fastapi.testclient import TestClient

    from ledgerline.main import create_app

    monkeypatch.setenv("LEDGERLINE_OPS_DIR", str(tmp_path))
    monkeypatch.setenv("LEDGERLINE_ENVIRONMENT", environment)
    for key, value in env.items():
        monkeypatch.setenv(f"LEDGERLINE_{key}", value)
    get_settings.cache_clear()
    routes._limiter = None
    return TestClient(create_app()).get("/healthz/deep").json()


@pytest.fixture(autouse=True)
def _reset(database_url):
    yield
    get_settings.cache_clear()
    routes._limiter = None


def test_production_health_fails_loudly_until_every_field_is_set(monkeypatch, tmp_path):
    body = deep(monkeypatch, tmp_path, "production")
    assert body["status"] == "fail"
    assert body["checks"]["legal"] == {"ok": False, "missing": [
        "legal_name", "kvk_number", "address", "contact_email"]}
    partial = deep(monkeypatch, tmp_path, "production",
                   OPERATOR_LEGAL_NAME="X B.V.", OPERATOR_CONTACT_EMAIL="a@x.example")
    assert partial["checks"]["legal"]["missing"] == ["kvk_number", "address"]


def test_a_value_that_is_still_a_bracketed_placeholder_counts_as_unset(monkeypatch, tmp_path):
    body = deep(monkeypatch, tmp_path, "production",
                **{**{k: v for k, v in IDENTITY.items()}, "OPERATOR_ADDRESS": "[ADDRESS]"})
    assert body["checks"]["legal"] == {"ok": False, "missing": ["address"]}


def test_production_legal_check_passes_when_all_set(monkeypatch, tmp_path):
    body = deep(monkeypatch, tmp_path, "production", **IDENTITY)
    assert body["checks"]["legal"] == {"ok": True}


def test_development_does_not_require_identity(monkeypatch, tmp_path):
    assert deep(monkeypatch, tmp_path, "development")["checks"]["legal"] == {"ok": True}


def test_check_never_returns_the_values(monkeypatch, tmp_path):
    settings = get_settings()
    ok, detail = health.check_operator_identity(settings)
    assert "KvK" not in str(detail)
