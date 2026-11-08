"""Route-level isolation and roles, generated from the application's own route table."""
from functools import cache
from importlib import import_module
from string import Formatter

import pytest
from fastapi.routing import APIRoute
from sqlalchemy import select, text

from ledgerline.main import MODULES
from ledgerline.models import (
    Account,
    ApiToken,
    BankAccount,
    BankTransaction,
    Contact,
    InboxDocument,
    JournalEntry,
    Membership,
    PurchaseInvoice,
    SalesInvoice,
)
from tests.conftest import _login, _user, tenant_db


def _flat(router, prefix=""):
    for r in router.routes:
        if hasattr(r, "original_router"):
            yield from _flat(r.original_router, prefix + r.include_context.prefix)
        elif isinstance(r, APIRoute):
            yield prefix + r.path, r


def all_routes() -> list[tuple[str, str]]:
    found = [(path, m) for module in MODULES for path, r in _flat(import_module(module).router)
             for m in sorted(r.methods)]
    assert len(found) > 80
    return found


NOT_ENTITIES = {
    ("/reports/aging/{kind}", "kind"): "sales",
    ("/setup/{key}", "key"): "business",
    # Covered by tests/account: the link token or the caller's own invitation, not a tenant row.
    ("/invite/{token}", "token"): "x",
    ("/reset/{token}", "token"): "x",
    ("/account/invitations/{membership_id}/{decision}", "membership_id"): "1",
    ("/account/invitations/{membership_id}/{decision}", "decision"): "accept",
}


def entity(path: str, param: str):
    if param == "entry_id":
        return JournalEntry
    if param == "invoice_id":
        return SalesInvoice if path.startswith("/sales") else PurchaseInvoice
    if param == "account_id":
        return BankAccount if path.startswith("/bank") else Account
    if param == "tx_id":
        return BankTransaction
    if param == "contact_id":
        return Contact
    if param == "document_id":
        return InboxDocument
    if param == "token_id":
        return ApiToken
    if param == "user_id":
        return "user"
    raise AssertionError(f"path parameter {param} of {path} has no tenant isolation test")


def fill(path: str, ids: dict) -> str:
    values = {}
    for _, param, _, _ in Formatter().parse(path):
        if param:
            values[param] = NOT_ENTITIES.get((path, param)) or ids[entity(path, param)]
    return path.format(**values)


@cache
def _post_routes() -> dict[str, APIRoute]:
    return {p: r for module in MODULES for p, r in _flat(import_module(module).router)
            if "POST" in r.methods}


def _body(path: str) -> tuple[dict, dict]:
    """Plausible form values for every body parameter, so validation never answers first."""
    route = _post_routes()[path]
    data, files = {}, {}
    for field in route.dependant.body_params:
        kind = str(field.field_info.annotation)
        if "UploadFile" in kind:
            files[field.name] = ("a.xml", b"<x/>")
        elif "int" in kind:
            data[field.name] = "1"
        elif "date" in kind:
            data[field.name] = "2026-01-01"
        else:
            data[field.name] = "x"
    return data, files


def call(client, method: str, url: str, path: str | None = None):
    if method == "GET":
        return client.get(url, follow_redirects=False)
    data, files = _body(path) if path else ({}, {})
    return client.post(url, data={**data, "csrf_token": client.csrf}, files=files or None,
                       follow_redirects=False)


ID_ROUTES = [(p, m) for p, m in all_routes()
             if "{" in p and p not in {n[0] for n in NOT_ENTITIES}]


@pytest.mark.parametrize(("path", "method"), ID_ROUTES, ids=lambda v: str(v))
def test_other_tenants_ids_are_not_found(client, seeded_a, seeded_b, path, method):
    response = call(client, method, fill(path, seeded_b["ids"]), path)
    assert response.status_code in (403, 404), (path, response.status_code)
    assert "Noordlicht" not in response.text


@pytest.mark.parametrize(("path", "method"), [r for r in ID_ROUTES if r[1] == "GET"],
                         ids=lambda v: str(v))
def test_own_ids_are_found(client, seeded_a, seeded_b, path, method):
    """Positive control: the same route answers for the tenant's own id."""
    client.post("/administration/switch", data={
        "administration_id": seeded_a["administration_id"], "csrf_token": client.csrf})
    response = call(client, method, fill(path, seeded_a["ids"]), path)
    if path.endswith("/attachment"):
        assert response.status_code == 404
    else:
        assert response.status_code == 200, (path, response.status_code)


def test_route_table_is_covered():
    assert {p for p, _ in ID_ROUTES} >= {"/sales/{invoice_id}", "/ledger/{entry_id}",
                                          "/inbox/{document_id}", "/contacts/{contact_id}"}
    for path, _ in ID_ROUTES:
        assert path not in {n[0] for n in NOT_ENTITIES}
        fill(path, {m: 1 for m in (Account, ApiToken, BankAccount, BankTransaction, Contact,
                                    InboxDocument, JournalEntry, PurchaseInvoice, SalesInvoice,
                                    "user")})


POSTS_OPEN_TO_EVERYONE = {
    "/login", "/tenants", "/logout", "/administration/switch", "/api/v1/inbox",
    "/login/totp", "/forgot", "/reset/{token}", "/invite/{token}", "/account/password",
    "/account/sign-out-everywhere", "/account/invitations/{membership_id}/{decision}",
    "/account/security/2fa/start", "/account/security/2fa/enable",
    "/account/security/2fa/disable",
}
POST_ROUTES = [p for p, m in all_routes() if m == "POST"]
MANAGER_ONLY = {"/settings/users", "/settings/require-2fa", "/settings/devices",
                "/settings/users/{user_id}/deactivate", "/settings/devices/{token_id}/revoke"}


@pytest.mark.parametrize("path", [p for p in POST_ROUTES if p not in POSTS_OPEN_TO_EVERYONE])
def test_accountant_cannot_write(readonly_client, path):
    url = path.format_map({k: 1 for k in ("entry_id", "invoice_id", "account_id", "tx_id",
                                           "contact_id", "document_id", "token_id", "user_id")})
    response = call(readonly_client, "POST", url, path)
    assert response.status_code == 403, (path, response.status_code)


@pytest.mark.parametrize("path", sorted(MANAGER_ONLY))
def test_bookkeeper_cannot_manage_users_or_devices(bookkeeper_client, path):
    url = path.format_map({"user_id": 1, "token_id": 1})
    assert call(bookkeeper_client, "POST", url, path).status_code == 403


def test_bookkeeper_can_write_the_books(bookkeeper_client, admin):
    response = bookkeeper_client.post(
        "/contacts/new", data={"name": "Klant", "kind": "customer",
                               "csrf_token": bookkeeper_client.csrf}, follow_redirects=False)
    assert response.status_code == 303
    assert bookkeeper_client.get("/settings/devices").status_code == 403


def test_owner_manages_users_and_devices(client, admin):
    assert client.get("/settings/devices").status_code == 200
    assert call(client, "POST", "/settings/devices", "/settings/devices").status_code in (200, 303)


def test_switch_to_other_tenants_administration_is_not_found(client, admin, other_admin):
    r = client.post("/administration/switch",
                    data={"administration_id": other_admin.id, "csrf_token": client.csrf},
                    follow_redirects=False)
    assert r.status_code == 404
    page = client.get("/settings").text
    assert f'value="{other_admin.id}"' not in page
    assert f'value="{admin.id}" selected' in page


def test_users_of_other_tenants_are_not_listed_or_changed(client, admin, other_owner):
    assert other_owner.email not in client.get("/settings").text
    r = client.post(f"/settings/users/{other_owner.id}/deactivate",
                    data={"csrf_token": client.csrf})
    assert r.status_code == 404


def test_session_without_membership_is_sent_back(client, session, owner, tenant):
    assert client.get("/sales").status_code == 200
    session.execute(Membership.__table__.delete().where(Membership.user_id == owner.id))
    session.commit()
    r = client.get("/sales", follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"] == "/tenants"


def _sales_form(client, contact_id, account_id):
    return client.post("/sales/new", data={
        "contact_id": contact_id, "issue_date": "2026-11-01", "due_date": "2026-12-01",
        "line_description": "Werk", "line_qty": "1", "line_price": "100,00",
        "line_vat_code": "V21", "line_account": account_id, "csrf_token": client.csrf,
    }, follow_redirects=False)


def test_forms_cannot_reference_another_tenants_rows(client, session, admin, seeded_b,
                                                     owner_engine):
    own_contact = Contact(administration_id=admin.id, name="Eigen klant")
    session.add(own_contact)
    session.commit()
    own_account = session.scalar(select(Account.id).where(
        Account.administration_id == admin.id, Account.code == "8000"))
    assert _sales_form(client, own_contact.id, own_account).status_code == 303
    foreign_contact = _sales_form(client, seeded_b["ids"][Contact], own_account)
    foreign_account = _sales_form(client, own_contact.id, seeded_b["ids"][Account])
    assert foreign_contact.status_code == 422 and foreign_account.status_code == 422
    with owner_engine.connect() as conn:
        count = conn.execute(text("SELECT count(*) FROM sales_invoices WHERE tenant_id = :t"),
                             {"t": admin.tenant_id}).scalar()
    assert count == 1


def test_helper_users_do_not_leak(session, tenant, other_tenant):
    user = _user(session, "dual@example.com", "owner")
    with tenant_db(other_tenant.id) as s:
        s.add(Membership(user_id=user.id, tenant_id=other_tenant.id, role="accountant"))
        s.commit()
    assert _login(user.email).get("/", follow_redirects=False).headers["location"] == "/tenants"
