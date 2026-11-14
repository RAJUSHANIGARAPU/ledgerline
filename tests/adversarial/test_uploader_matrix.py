"""Role matrix for the capture-only uploader, generated from the registered routes.

A route added later is denied to the uploader unless it is listed in ALLOWED, so granting access
is always an explicit edit here.
"""

import os
import re

import pytest
from fastapi import routing

os.environ.setdefault("LEDGERLINE_SECRET_KEY", "test-secret-key-not-for-production")

from ledgerline.main import create_app  # noqa: E402

# Reachable without a tenant role: public pages, or login-level pages that only touch the
# signed-in person's own account. /capture is the only tenant-scoped page an uploader gets.
PUBLIC = {
    "/healthz", "/healthz/deep", "/login", "/login/totp", "/forgot", "/invite/{token}",
    "/reset/{token}", "/legal/privacy", "/legal/terms", "/legal/cookies", "/legal/disclaimer",
    "/manifest.webmanifest", "/sw.js", "/api/v1/inbox",
}
ACCOUNT_LEVEL = {"/tenants", "/account", "/account/security"}
CAPTURE = {"/capture"}
ALLOWED = PUBLIC | ACCOUNT_LEVEL | CAPTURE


def _walk(routes):
    for route in routes:
        if isinstance(route, routing._IncludedRouter):
            yield from _walk(route.effective_route_contexts())
        else:
            yield route


def registered():
    found = []
    for route in _walk(create_app().routes):
        methods = getattr(route, "methods", None) or getattr(
            getattr(route, "starlette_route", None), "methods", None) or set()
        for method in methods - {"HEAD", "OPTIONS"}:
            found.append((method, route.path))
    return sorted(found)


def concrete(path: str) -> str:
    return re.sub(r"\{[^}]+\}", "1", path)


def is_denied(response) -> bool:
    if response.status_code == 403:
        return True
    return response.status_code == 303 and response.headers["location"] == "/capture"


ROUTES = registered()
DENIED_GET = [p for m, p in ROUTES if m == "GET" and p not in ALLOWED]
DENIED_WRITE = [(m, p) for m, p in ROUTES if m != "GET" and not (
    p in ALLOWED or p.startswith(("/account/", "/login/", "/forgot", "/reset/", "/invite/"))
    or p in {"/logout", "/tenants"})]


def test_matrix_sees_the_whole_route_table():
    assert len(ROUTES) > 100 and len(DENIED_GET) > 40 and len(DENIED_WRITE) > 40
    assert ("GET", "/capture") in ROUTES and ("POST", "/capture") in ROUTES


@pytest.mark.parametrize("path", DENIED_GET)
def test_uploader_cannot_read(uploader_client, path):
    response = uploader_client.get(concrete(path), follow_redirects=False)
    assert is_denied(response), (path, response.status_code)


@pytest.mark.parametrize("path", DENIED_GET)
def test_uploader_htmx_read_is_403_not_redirect(uploader_client, path):
    response = uploader_client.get(
        concrete(path), headers={"hx-request": "true"}, follow_redirects=False)
    assert response.status_code == 403, (path, response.status_code)


@pytest.mark.parametrize(("method", "path"), DENIED_WRITE)
def test_uploader_cannot_write(uploader_client, method, path):
    response = uploader_client.request(
        method, concrete(path), data={"csrf_token": uploader_client.csrf},
        headers={"x-csrf-token": uploader_client.csrf}, follow_redirects=False)
    assert is_denied(response), (method, path, response.status_code)


def test_uploader_reaches_only_capture(uploader_client):
    assert uploader_client.get("/capture").status_code == 200
    for path in ("/tenants", "/account", "/account/security"):
        assert uploader_client.get(path, follow_redirects=False).status_code in (200, 303), path
