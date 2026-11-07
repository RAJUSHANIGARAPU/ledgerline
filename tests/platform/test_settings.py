from sqlalchemy import select

from ledgerline.auth import hasher
from ledgerline.models import Administration, Role, User
from tests.conftest import OWNER_PASSWORD

ADMIN_FORM = {"name": "Nieuwe Naam BV", "legal_form": "bv", "kvk_number": "99887766",
              "vat_number": "NL999999999B01", "iban": "NL91ABNA0417164300", "bic": "ABNANL2A",
              "email": "info@nieuw.example", "address_line": "Kade 1", "postcode": "1000 AA",
              "city": "Rotterdam", "country": "NL", "invoice_prefix": "NN-",
              "payment_terms_days": "14", "kor": "on"}


def test_update_administration(client, session, admin):
    r = client.post("/settings", data={**ADMIN_FORM, "csrf_token": client.csrf},
                    follow_redirects=False)
    assert r.status_code == 303
    session.expire_all()
    a = session.get(Administration, admin.id)
    assert (a.name, a.legal_form, a.payment_terms_days, a.invoice_prefix) == (
        "Nieuwe Naam BV", "bv", 14, "NN-")
    assert a.kor is True and a.city == "Rotterdam"


def test_update_rejects_invalid_values_and_keeps_old(client, session, admin):
    r = client.post("/settings", data={**ADMIN_FORM, "vat_number": "NL1", "payment_terms_days": "x",
                                       "csrf_token": client.csrf})
    assert r.status_code == 422
    assert "VAT number does not match" in r.text and "whole number of days" in r.text
    session.expire_all()
    assert session.get(Administration, admin.id).name == "Testbedrijf"


def test_kor_can_be_switched_off(client, session, admin):
    client.post("/settings", data={**ADMIN_FORM, "csrf_token": client.csrf})
    form = {k: v for k, v in ADMIN_FORM.items() if k != "kor"}
    client.post("/settings", data={**form, "csrf_token": client.csrf})
    session.expire_all()
    assert session.get(Administration, admin.id).kor is False


def test_create_additional_administration_and_switch(client, session, admin):
    r = client.post("/settings/administrations",
                    data={"new_name": "Tweede BV", "new_legal_form": "bv",
                          "csrf_token": client.csrf}, follow_redirects=False)
    assert r.status_code == 303
    second = session.scalar(select(Administration).where(Administration.name == "Tweede BV"))
    assert second is not None
    assert f'value="{second.id}" selected' in client.get("/settings").text
    client.post("/administration/switch",
                data={"administration_id": admin.id, "csrf_token": client.csrf})
    assert f'value="{admin.id}" selected' in client.get("/settings").text


def test_owner_creates_accountant_who_is_read_only(client, session, admin):
    r = client.post("/settings/users", data={
        "email": "Boekhouder@Example.com", "role": "accountant",
        "password": "twelve-chars-ok", "confirm": "twelve-chars-ok",
        "csrf_token": client.csrf}, follow_redirects=False)
    assert r.status_code == 303
    user = session.scalar(select(User).where(User.email == "boekhouder@example.com"))
    assert user.role == Role.ACCOUNTANT
    hasher.verify(user.password_hash, "twelve-chars-ok")


def test_short_password_rejected_for_new_user(client, session, admin):
    client.post("/settings/users", data={"email": "x@example.com", "role": "accountant",
                                         "password": "short", "confirm": "short",
                                         "csrf_token": client.csrf})
    assert session.scalar(select(User).where(User.email == "x@example.com")) is None


def test_deactivate_user_but_not_self(client, session, admin, owner):
    other = User(email="weg@example.com", password_hash="x", role=Role.ACCOUNTANT)
    session.add(other)
    session.commit()
    client.post(f"/settings/users/{other.id}/deactivate", data={"csrf_token": client.csrf})
    client.post(f"/settings/users/{owner.id}/deactivate", data={"csrf_token": client.csrf})
    session.expire_all()
    assert session.get(User, other.id).is_active is False
    assert session.get(User, owner.id).is_active is True


def test_change_own_password(client, session, owner):
    new = "a-brand-new-passphrase"
    r = client.post("/settings/password", data={"current": OWNER_PASSWORD, "password": new,
                                                "confirm": new, "csrf_token": client.csrf},
                    follow_redirects=False)
    assert r.status_code == 303
    session.expire_all()
    hasher.verify(session.get(User, owner.id).password_hash, new)


def test_change_password_needs_current_password(client, session, owner):
    before = owner.password_hash
    client.post("/settings/password", data={"current": "wrong-wrong-wrong", "password": "x" * 14,
                                            "confirm": "x" * 14, "csrf_token": client.csrf})
    session.expire_all()
    assert session.get(User, owner.id).password_hash == before


def test_accountant_may_change_own_password(readonly_client, session):
    new = "accountant-new-pass"
    r = readonly_client.post("/settings/password", data={
        "current": OWNER_PASSWORD, "password": new, "confirm": new,
        "csrf_token": readonly_client.csrf}, follow_redirects=False)
    assert r.status_code == 303
    user = session.scalar(select(User).where(User.email == "accountant@example.com"))
    session.refresh(user)
    hasher.verify(user.password_hash, new)
