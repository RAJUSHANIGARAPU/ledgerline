import re

import pyotp
import pytest

from ledgerline.accounts import ratelimit, totp
from tests.account.conftest import (
    OWNER_PASSWORD,
    enable_2fa,
    password_login,
    read_secret,
    submit_code,
    totp_code,
)


def post(client, path, **data):
    return client.post(path, data={**data, "csrf_token": client.csrf}, follow_redirects=False)


def test_secret_roundtrip_and_ciphertext_differs():
    secret = totp.new_secret()
    encrypted = totp.encrypt_secret(secret)
    assert secret not in encrypted and totp.decrypt_secret(encrypted) == secret


def test_secret_cannot_be_decrypted_with_another_key(monkeypatch):
    from ledgerline.config import get_settings

    encrypted = totp.encrypt_secret("JBSWY3DPEHPK3PXP")
    monkeypatch.setenv("LEDGERLINE_SECRET_KEY", "some-other-key")
    get_settings.cache_clear()
    with pytest.raises(Exception):  # noqa: B017 - cryptography InvalidToken
        totp.decrypt_secret(encrypted)


def test_enrolment_shows_qr_and_secret_and_enables_only_after_a_valid_code(client, clock, q):
    post(client, "/account/security/2fa/start")
    page = client.get("/account/security").text
    assert re.search(r'<img src="data:image/svg\+xml;base64,[A-Za-z0-9+/=]+"', page)
    secret = read_secret(client)
    assert q("SELECT enabled_at FROM user_totp")[0][0] is None
    bad = post(client, "/account/security/2fa/enable", code="000000")
    assert bad.status_code == 303
    assert q("SELECT enabled_at FROM user_totp")[0][0] is None
    good = post(client, "/account/security/2fa/enable", code=totp_code(secret, clock.t))
    assert good.status_code == 200
    assert q("SELECT enabled_at FROM user_totp")[0][0] is not None


def test_secret_and_recovery_codes_are_not_stored_in_plaintext(client, clock, q):
    secret, codes = enable_2fa(client, clock)
    assert len(codes) == 10 and len(set(codes)) == 10
    stored = q("SELECT secret_enc FROM user_totp")[0][0]
    assert secret not in stored
    hashes = [r[0] for r in q("SELECT code_hash FROM recovery_codes")]
    assert len(hashes) == 10 and all(re.fullmatch(r"[0-9a-f]{64}", h) for h in hashes)
    dump = " ".join(hashes) + stored
    assert not any(c.replace("-", "") in dump for c in codes) and not any(c in dump for c in codes)


def test_second_step_is_required_and_session_is_not_logged_in_before_it(client, owner, clock):
    enable_2fa(client, clock)
    fresh, response = password_login(owner.email)
    assert response.headers["location"] == "/login/totp"
    assert "user_id" not in _session(fresh) and "pw_ok" in _session(fresh)
    assert fresh.get("/sales", follow_redirects=False).headers["location"] == "/login"


def _session(client):
    import base64
    import json

    raw = client.cookies.get("ledgerline_session").split(".")[0]
    return json.loads(base64.b64decode(raw + "=" * (-len(raw) % 4)))


def test_login_with_code_succeeds(client, owner, clock):
    secret, _ = enable_2fa(client, clock)
    clock.advance(seconds=30)
    fresh, _ = password_login(owner.email)
    r = submit_code(fresh, totp_code(secret, clock.t))
    assert r.status_code == 303 and r.headers["location"] == "/"
    assert fresh.get("/sales").status_code == 200


def test_wrong_code_is_refused(client, owner, clock):
    enable_2fa(client, clock)
    clock.advance(seconds=30)
    fresh, _ = password_login(owner.email)
    assert submit_code(fresh, "123456").status_code == 401
    assert fresh.get("/sales", follow_redirects=False).headers["location"] == "/login"


@pytest.mark.parametrize(("offset", "accepted"), [(-60, False), (-30, True), (0, True),
                                                  (30, True), (60, False)])
def test_drift_of_one_step_is_accepted_two_is_not(client, owner, clock, offset, accepted):
    secret, _ = enable_2fa(client, clock)
    clock.advance(seconds=120)
    fresh, _ = password_login(owner.email)
    from datetime import timedelta

    r = submit_code(fresh, totp_code(secret, clock.t + timedelta(seconds=offset)))
    assert (r.status_code == 303) is accepted


def test_a_code_cannot_be_used_twice(client, owner, clock):
    secret, _ = enable_2fa(client, clock)
    clock.advance(seconds=30)
    code = totp_code(secret, clock.t)
    first, _ = password_login(owner.email)
    assert submit_code(first, code).status_code == 303
    second, _ = password_login(owner.email)
    assert submit_code(second, code).status_code == 401
    clock.advance(seconds=30)
    third, _ = password_login(owner.email)
    assert submit_code(third, totp_code(secret, clock.t)).status_code == 303


def test_the_enrolment_code_cannot_be_replayed_to_log_in(client, owner, clock):
    secret, _ = enable_2fa(client, clock)
    fresh, _ = password_login(owner.email)
    assert submit_code(fresh, totp_code(secret, clock.t)).status_code == 401


def test_an_older_step_is_refused_after_a_newer_one_was_used(client, owner, clock):
    secret, _ = enable_2fa(client, clock)
    clock.advance(seconds=60)
    a, _ = password_login(owner.email)
    assert submit_code(a, totp_code(secret, clock.t)).status_code == 303
    b, _ = password_login(owner.email)
    from datetime import timedelta

    assert submit_code(b, totp_code(secret, clock.t - timedelta(seconds=30))).status_code == 401


def test_recovery_code_works_once(client, owner, clock, q):
    _, codes = enable_2fa(client, clock)
    first, _ = password_login(owner.email)
    assert submit_code(first, codes[0].upper()).status_code == 303
    second, _ = password_login(owner.email)
    assert submit_code(second, codes[0]).status_code == 401
    third, _ = password_login(owner.email)
    assert submit_code(third, codes[1]).status_code == 303
    assert q("SELECT count(*) FROM recovery_codes WHERE used_at IS NOT NULL")[0][0] == 2
    assert q("SELECT count(*) FROM audit_events WHERE action = '2fa.recovery_used'")[0][0] == 2


def test_five_wrong_codes_restart_the_login(client, owner, clock):
    secret, _ = enable_2fa(client, clock)
    clock.advance(seconds=30)
    fresh, _ = password_login(owner.email)
    for _ in range(4):
        assert submit_code(fresh, "111111").status_code == 401
    last = submit_code(fresh, "111111")
    assert last.status_code == 303 and last.headers["location"] == "/login"
    assert fresh.get("/login/totp", follow_redirects=False).headers["location"] == "/login"


def test_replaying_an_old_cookie_cannot_reset_the_attempt_counter(client, owner, clock):
    enable_2fa(client, clock)
    clock.advance(seconds=30)
    for _ in range(3):
        fresh, _ = password_login(owner.email)
        for _ in range(4):
            submit_code(fresh, "111111")
    again, _ = password_login(owner.email)
    assert ratelimit.totp_failures.blocked(str(owner.id))
    r = submit_code(again, "111111")
    assert r.status_code == 429


def test_password_step_marker_expires(client, owner, clock, monkeypatch):
    import time as time_module

    enable_2fa(client, clock)
    fresh, _ = password_login(owner.email)
    real = time_module.time()
    monkeypatch.setattr("ledgerline.accounts.routes.time.time", lambda: real + 400)
    assert fresh.get("/login/totp", follow_redirects=False).headers["location"] == "/login"


def test_reset_keeps_two_factor(client, owner, clock, outbox):
    from tests.account.test_reset import anon_client, forgot, reset

    enable_2fa(client, clock)
    anon = anon_client()
    forgot(anon, owner.email)
    from tests.account.conftest import link_in, path_of

    reset(anon, path_of(link_in(outbox[0])), "yet-another-passphrase")
    _, response = password_login(owner.email, "yet-another-passphrase")
    assert response.headers["location"] == "/login/totp"


def test_disable_needs_password_and_current_code(client, owner, clock, q):
    secret, _ = enable_2fa(client, clock)
    clock.advance(seconds=30)
    post(client, "/account/security/2fa/disable", password="wrong-wrong-wrong",
         code=totp_code(secret, clock.t))
    assert q("SELECT count(*) FROM user_totp")[0][0] == 1
    post(client, "/account/security/2fa/disable", password=OWNER_PASSWORD, code="000000")
    assert q("SELECT count(*) FROM user_totp")[0][0] == 1
    post(client, "/account/security/2fa/disable", password=OWNER_PASSWORD,
         code=totp_code(secret, clock.t))
    assert q("SELECT count(*) FROM user_totp")[0][0] == 0
    assert q("SELECT count(*) FROM recovery_codes")[0][0] == 0
    actions = [r[0] for r in q("SELECT action FROM audit_events WHERE action LIKE '2fa.%' "
                               "ORDER BY id")]
    assert actions == ["2fa.enabled", "2fa.disabled"]


def test_owner_must_have_two_factor_before_requiring_it(client, q):
    r = post(client, "/settings/require-2fa", enforce="on")
    assert r.status_code == 303
    assert q("SELECT require_2fa FROM tenants")[0][0] is False


def test_required_two_factor_sends_members_to_enrolment(client, owner, bookkeeper_client,
                                                        clock, q):
    enable_2fa(client, clock)
    post(client, "/settings/require-2fa", enforce="on")
    assert q("SELECT require_2fa FROM tenants")[0][0] is True
    r = bookkeeper_client.get("/sales", follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"] == "/account/security?required=1"
    assert "requires two-factor" in bookkeeper_client.get("/account/security?required=1").text
    secret, _ = enable_2fa(bookkeeper_client, clock)
    assert bookkeeper_client.get("/sales").status_code == 200


def test_required_two_factor_blocks_disabling_it(client, clock, q):
    secret, _ = enable_2fa(client, clock)
    post(client, "/settings/require-2fa", enforce="on")
    clock.advance(seconds=30)
    post(client, "/account/security/2fa/disable", password=OWNER_PASSWORD,
         code=totp_code(secret, clock.t))
    assert q("SELECT count(*) FROM user_totp")[0][0] == 1


def test_account_page_lists_memberships_and_two_factor_status(client, clock):
    page = client.get("/account").text
    assert "Tenant A" in page and "owner@example.com" in page and "<strong>off</strong>" in page
    enable_2fa(client, clock)
    assert "<strong>on</strong>" in client.get("/account").text


def test_accountant_can_enrol_two_factor(readonly_client, clock):
    secret, codes = enable_2fa(readonly_client, clock)
    assert len(codes) == 10 and secret


def test_audit_logs_login_failures_and_2fa_events_without_secrets(client, owner, clock, q):
    secret, codes = enable_2fa(client, clock)
    password_login(owner.email, "wrong-wrong-wrong")
    clock.advance(seconds=30)
    fresh, _ = password_login(owner.email)
    submit_code(fresh, "000000")
    rows = q("SELECT action, details::text AS d FROM audit_events ORDER BY id")
    actions = [r.action for r in rows]
    assert "2fa.enabled" in actions and actions.count("login.failure") == 2
    blob = " ".join(f"{r.action} {r.d}" for r in rows)
    assert secret not in blob and not any(c in blob for c in codes)
    assert "wrong-wrong-wrong" not in blob and OWNER_PASSWORD not in blob


def test_defaults_match_the_rfc_6238_sha1_vector():
    from datetime import UTC, datetime

    t = pyotp.TOTP("GEZDGNBVGY3TQOJQGEZDGNBVGY3TQOJQ")
    assert t.at(datetime.fromtimestamp(59, UTC)) == "287082"
    assert totp.STEP_SECONDS == 30
