import re

import pytest
from fastapi.testclient import TestClient

from ledgerline.i18n import COOKIE
from ledgerline.main import create_app
from tests.mobile.test_uploader import upload


@pytest.fixture
def anon(database_url):
    return TestClient(create_app())


def test_login_is_english_by_default(anon):
    html = anon.get("/login").text
    assert '<html lang="en">' in html and "Sign in" in html and "Forgot password?" in html


def test_login_renders_dutch_with_the_cookie(anon):
    anon.cookies.set(COOKIE, "nl")
    html = anon.get("/login").text
    assert '<html lang="nl">' in html
    assert "Inloggen" in html and "Wachtwoord vergeten?" in html and "Forgot password?" not in html


def test_login_renders_dutch_with_accept_language(anon):
    html = anon.get("/login", headers={"Accept-Language": "nl-NL,nl;q=0.9,en;q=0.8"}).text
    assert '<html lang="nl">' in html and "Wachtwoord vergeten?" in html


def test_cookie_beats_accept_language(anon):
    anon.cookies.set(COOKIE, "en")
    html = anon.get("/login", headers={"Accept-Language": "nl"}).text
    assert '<html lang="en">' in html


def test_unsupported_cookie_value_falls_back_to_the_header(anon):
    anon.cookies.set(COOKIE, "xx")
    assert '<html lang="nl">' in anon.get("/login", headers={"Accept-Language": "nl"}).text


def test_login_errors_are_dutch(anon, owner):
    anon.cookies.set(COOKIE, "nl")
    token = re.search(r'name="csrf_token" value="([^"]+)"', anon.get("/login").text).group(1)
    response = anon.post("/login", data={"email": owner.email, "password": "wrong-password-x",
                                         "csrf_token": token})
    assert response.status_code == 401 and "Onjuist e-mailadres of wachtwoord." in response.text


def test_switch_sets_the_cookie_and_returns_to_the_page(anon):
    token = re.search(r'name="csrf_token" value="([^"]+)"', anon.get("/login").text).group(1)
    response = anon.post("/language", data={"lang": "nl", "next": "/login", "csrf_token": token},
                         follow_redirects=False)
    assert response.status_code == 303 and response.headers["location"] == "/login"
    assert "ledgerline_lang=nl" in response.headers["set-cookie"]
    assert "Wachtwoord vergeten?" in anon.get("/login").text


@pytest.mark.parametrize("target", ["https://evil.example/", "//evil.example", "/\\evil"])
def test_switch_never_redirects_off_site(anon, target):
    token = re.search(r'name="csrf_token" value="([^"]+)"', anon.get("/login").text).group(1)
    response = anon.post("/language", data={"lang": "en", "next": target, "csrf_token": token},
                         follow_redirects=False)
    assert response.headers["location"] == "/"


def test_switch_rejects_unknown_language_and_missing_csrf(anon):
    token = re.search(r'name="csrf_token" value="([^"]+)"', anon.get("/login").text).group(1)
    assert anon.post("/language", data={"lang": "de", "csrf_token": token}).status_code == 400
    assert anon.post("/language", data={"lang": "nl"}).status_code == 403


def test_capture_is_dutch_with_the_cookie(uploader_client):
    uploader_client.cookies.set(COOKIE, "nl")
    html = uploader_client.get("/capture").text
    assert '<html lang="nl">' in html
    assert "Foto maken" in html and "Uploaden naar inbox" in html and "Uitloggen" in html
    assert "Take photo" not in html and "Sign out" not in html
    assert '"retry": "Opnieuw proberen"' in html


def test_capture_is_dutch_with_accept_language(uploader_client):
    html = uploader_client.get("/capture", headers={"Accept-Language": "nl"}).text
    assert "Bestand kiezen" in html and "Choose file" not in html


def test_capture_is_english_by_default_and_passes_english_strings_to_the_script(uploader_client):
    html = uploader_client.get("/capture").text
    assert "Take photo" in html and "Upload to inbox" in html
    assert '"retry": "Retry"' in html


def test_captured_result_is_dutch(uploader_client):
    uploader_client.cookies.set(COOKIE, "nl")
    first = upload(uploader_client, "dutch")
    assert "In je inbox:" in first.text and "Nog een uploaden" in first.text
    again = upload(uploader_client, "dutch")
    assert "staat al in je inbox" in again.text


def test_capture_rejection_is_dutch(uploader_client):
    uploader_client.cookies.set(COOKIE, "nl")
    files = [("files", ("x.txt", b"plain text", "text/plain"))]
    response = uploader_client.post("/capture", data={"csrf_token": uploader_client.csrf},
                                    files=files)
    assert "Dit bestandstype wordt niet ondersteund" in response.text


def test_json_rejection_for_the_offline_queue_follows_the_language(uploader_client):
    files = [("files", ("x.txt", b"plain text", "text/plain"))]
    response = uploader_client.post(
        "/capture", data={"csrf_token": uploader_client.csrf}, files=files,
        headers={"Accept": "application/json", "Accept-Language": "nl"})
    assert response.json()["results"][0]["error"].startswith("Dit bestandstype")


def test_photo_privacy_note_is_dutch_on_capture_and_inbox(client, monkeypatch):
    from ledgerline.config import get_settings

    monkeypatch.setattr(get_settings(), "extraction_provider", "anthropic")
    client.cookies.set(COOKIE, "nl")
    for path in ("/capture", "/inbox"):
        html = client.get(path).text
        assert "loonstroken" in html, path


def test_inbox_upload_form_is_dutch(client):
    client.cookies.set(COOKIE, "nl")
    html = client.get("/inbox").text
    assert "Facturen toevoegen" in html and "Sleep bestanden hierheen" in html
