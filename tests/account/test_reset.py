import re

from fastapi.testclient import TestClient

from ledgerline.main import create_app
from tests.account.conftest import OWNER_PASSWORD, link_in, password_login, path_of

NEW = "another-fresh-passphrase"


def anon_client():
    client = TestClient(create_app())
    client.csrf = re.search(r'name="csrf_token" value="([^"]+)"',
                            client.get("/forgot").text).group(1)
    return client


def forgot(client, email):
    r = client.post("/forgot", data={"email": email, "csrf_token": client.csrf},
                    follow_redirects=False)
    page = client.get(r.headers["location"]).text
    notice = re.search(r'class="flash flash-(\w+)" role="status">([^<]+)<', page).group(2)
    return r.status_code, r.headers["location"], notice


def reset(client, path, password=NEW, confirm=None):
    token = re.search(r'name="csrf_token" value="([^"]+)"', client.get("/forgot").text).group(1)
    return client.post(path, data={"password": password, "confirm": confirm or password,
                                   "csrf_token": token}, follow_redirects=False)


def test_forgot_gives_the_same_answer_for_known_and_unknown_emails(owner, outbox):
    client = anon_client()
    known = forgot(client, owner.email)
    unknown = forgot(client, "nobody@example.com")
    assert known == unknown
    assert "If an account exists" in known[2]
    assert [m["To"] for m in outbox] == [owner.email]


def test_reset_sets_password_and_token_is_single_use(owner, outbox, q):
    client = anon_client()
    forgot(client, owner.email)
    path = path_of(link_in(outbox[0]))
    assert reset(client, path).status_code == 303
    assert reset(client, path).status_code == 404
    assert password_login(owner.email, OWNER_PASSWORD)[1].status_code == 401
    assert password_login(owner.email, NEW)[1].status_code == 303
    row = q("SELECT purpose, used_at FROM auth_tokens")[0]
    assert row.purpose == "reset" and row.used_at is not None


def test_reset_invalidates_other_outstanding_tokens(owner, outbox):
    client = anon_client()
    forgot(client, owner.email)
    forgot(client, owner.email)
    first, second = (path_of(link_in(m)) for m in outbox)
    assert reset(client, second).status_code == 303
    assert client.get(first).status_code == 404


def test_reset_logs_out_existing_sessions(client, owner, outbox):
    assert client.get("/sales").status_code == 200
    anon = anon_client()
    forgot(anon, owner.email)
    reset(anon, path_of(link_in(outbox[0])))
    r = client.get("/sales", follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"] == "/login"


def test_reset_token_expires_after_an_hour(owner, outbox, clock):
    anon = anon_client()
    forgot(anon, owner.email)
    path = path_of(link_in(outbox[0]))
    clock.advance(minutes=59)
    assert anon.get(path).status_code == 200
    clock.advance(minutes=2)
    assert anon.get(path).status_code == 404
    assert reset(anon, path).status_code == 404


def test_reset_rejects_weak_passwords_and_keeps_the_token(owner, outbox):
    anon = anon_client()
    forgot(anon, owner.email)
    path = path_of(link_in(outbox[0]))
    for password, confirm in (("short", "short"), (owner.email, owner.email),
                              ("x" * 16, "x" * 16), (NEW, "mismatch-mismatch")):
        assert reset(anon, path, password, confirm).status_code == 422
    assert reset(anon, path).status_code == 303


def test_unknown_or_tampered_reset_token(owner, outbox):
    anon = anon_client()
    forgot(anon, owner.email)
    path = path_of(link_in(outbox[0]))
    assert anon.get("/reset/" + "y" * 43).status_code == 404
    assert anon.get(path + "x").status_code == 404


def test_rate_limit_per_email_hides_itself_and_stops_mail(owner, outbox):
    client = anon_client()
    answers = [forgot(client, owner.email) for _ in range(5)]
    assert len(set(answers)) == 1
    assert len(outbox) == 3


def test_rate_limit_per_ip_applies_across_emails(owner, outbox):
    client = anon_client()
    for i in range(12):
        forgot(client, f"user{i}@example.com")
    forgot(client, owner.email)
    assert outbox == []


def test_no_mail_for_unknown_address_and_deactivated_user(owner, outbox, owner_engine):
    from sqlalchemy import text

    with owner_engine.begin() as conn:
        conn.execute(text("UPDATE users SET is_active = false WHERE id = :i"), {"i": owner.id})
    client = anon_client()
    assert "If an account exists" in forgot(client, owner.email)[2]
    assert outbox == []


def test_change_password_signs_out_other_devices_but_not_this_one(client, owner):
    other, _ = password_login(owner.email)
    assert other.get("/sales").status_code == 200
    r = client.post("/account/password", data={
        "current": OWNER_PASSWORD, "password": NEW, "confirm": NEW, "csrf_token": client.csrf},
        follow_redirects=False)
    assert r.status_code == 303
    assert client.get("/sales").status_code == 200
    assert other.get("/sales", follow_redirects=False).headers["location"] == "/login"


def test_change_password_needs_current_password(client, owner, q):
    before = q("SELECT password_hash FROM users WHERE id = :i", i=owner.id)[0][0]
    client.post("/account/password", data={
        "current": "wrong-wrong-wrong", "password": NEW, "confirm": NEW,
        "csrf_token": client.csrf})
    assert q("SELECT password_hash FROM users WHERE id = :i", i=owner.id)[0][0] == before


def test_sign_out_everywhere_ends_all_sessions_including_this_one(client, owner):
    other, _ = password_login(owner.email)
    r = client.post("/account/sign-out-everywhere", data={"csrf_token": client.csrf},
                    follow_redirects=False)
    assert r.status_code == 303
    for c in (client, other):
        assert c.get("/sales", follow_redirects=False).headers["location"] == "/login"


def test_login_page_links_to_forgot_password():
    html = TestClient(create_app()).get("/login").text
    assert 'href="/forgot"' in html


def test_audit_has_reset_events_without_tokens(owner, outbox, q):
    client = anon_client()
    forgot(client, owner.email)
    link = link_in(outbox[0])
    reset(client, path_of(link))
    rows = q("SELECT action, details::text FROM audit_events WHERE action LIKE 'reset.%' "
             "ORDER BY id")
    assert [r[0] for r in rows] == ["reset.requested", "reset.completed"]
    assert all(link.rsplit("/", 1)[1] not in str(r) for r in rows)
