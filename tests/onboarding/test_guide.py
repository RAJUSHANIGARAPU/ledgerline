import re

from ledgerline.main import create_app
from ledgerline.onboarding.routes import GUIDE_PATH

LINK = re.compile(r"\[[^\]]+\]\((/[^)\s]*)\)")
PARALLEL = ("/inbox", "/capture", "/settings/devices")


def registered(path: str) -> bool:
    bare = path.split("?")[0].split("#")[0]
    return any(getattr(r, "path", None) == bare for r in create_app().routes)


def test_help_renders_guide_headings(client):
    r = client.get("/help")
    assert r.status_code == 200
    assert '<h2 id="getting-started">Getting started</h2>' in r.text
    assert 'href="#the-btw-quarter"' in r.text
    assert "<script>" not in r.text.split('data-testid="guide"')[1]


def test_help_is_xss_safe(client, monkeypatch, tmp_path):
    evil = tmp_path / "g.md"
    evil.write_text("# Hi\n<script>alert(1)</script>\n[x](javascript:alert(1))\n")
    monkeypatch.setattr("ledgerline.onboarding.routes.GUIDE_PATH", evil)
    body = client.get("/help").text.split('data-testid="guide"')[1]
    assert "<script>" not in body and "javascript:" not in body
    assert "&lt;script&gt;" in body


def test_help_without_guide_file(client, monkeypatch, tmp_path):
    monkeypatch.setattr("ledgerline.onboarding.routes.GUIDE_PATH", tmp_path / "missing.md")
    assert client.get("/help").status_code == 404


def test_every_internal_guide_link_resolves(client):
    links = sorted(set(LINK.findall(GUIDE_PATH.read_text(encoding="utf-8"))))
    assert len(links) > 15
    for link in links:
        if link.startswith(PARALLEL) and not registered(link):
            continue
        r = client.get(link, follow_redirects=True)
        assert r.status_code == 200, link
