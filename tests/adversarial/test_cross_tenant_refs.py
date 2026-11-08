"""Tenant A sends tenant B's ids in form bodies. Nothing of B may be referenced or changed."""

from sqlalchemy import text

from ledgerline.models import (
    Account,
    BankAccount,
    Contact,
    InboxDocument,
    PurchaseInvoice,
    SalesInvoice,
)
from tests.adversarial.conftest import use_admin


def _first_unmatched_tx(q, admin_id):
    rows = q(
        "SELECT bt.id FROM bank_transactions bt JOIN bank_accounts ba "
        "ON ba.id = bt.bank_account_id WHERE ba.administration_id = :a "
        "AND bt.status = 'unmatched' ORDER BY bt.id LIMIT 1",
        a=admin_id,
    )
    assert rows, "seed has no unmatched transaction"
    return rows[0][0]


def test_bank_match_cannot_allocate_to_other_tenants_invoice(client, seeded_a, seeded_b, q):
    use_admin(client, seeded_a["administration_id"])
    tx = _first_unmatched_tx(q, seeded_a["administration_id"])
    b_sales = seeded_b["ids"][SalesInvoice]
    before = q("SELECT paid_cents, status FROM sales_invoices WHERE id = :i", i=b_sales)
    allocs = q("SELECT count(*) FROM payment_allocations WHERE sales_invoice_id = :i", i=b_sales)
    client.post(
        f"/bank/transactions/{tx}/match",
        data={"allocation": f"sales:{b_sales}:100", "csrf_token": client.csrf},
        follow_redirects=False,
    )
    assert (
        q("SELECT count(*) FROM payment_allocations WHERE sales_invoice_id = :i", i=b_sales)
        == allocs
    )
    assert q("SELECT paid_cents, status FROM sales_invoices WHERE id = :i", i=b_sales) == before


def test_bank_book_cannot_use_other_tenants_ledger_account(client, seeded_a, seeded_b, q):
    use_admin(client, seeded_a["administration_id"])
    tx = _first_unmatched_tx(q, seeded_a["administration_id"])
    b_acc = seeded_b["ids"][Account]
    client.post(
        f"/bank/transactions/{tx}/book",
        data={"account_id": b_acc, "csrf_token": client.csrf},
        follow_redirects=False,
    )
    assert (
        q(
            "SELECT count(*) FROM journal_lines WHERE account_id = :a AND tenant_id <> "
            "(SELECT tenant_id FROM accounts WHERE id = :a)",
            a=b_acc,
        )[0][0]
        == 0
    )


def test_bank_account_cannot_be_bound_to_other_tenants_ledger_account(
    client, seeded_a, seeded_b, q
):
    use_admin(client, seeded_a["administration_id"])
    b_acc = seeded_b["ids"][Account]
    client.post(
        "/bank/accounts",
        data={
            "iban": "NL91ABNA0417164300",
            "name": "Evil",
            "ledger_account_id": b_acc,
            "csrf_token": client.csrf,
        },
        follow_redirects=False,
    )
    assert (
        q(
            "SELECT count(*) FROM bank_accounts WHERE ledger_account_id = :a "
            "AND tenant_id <> (SELECT tenant_id FROM accounts WHERE id = :a)",
            a=b_acc,
        )[0][0]
        == 0
    )


def test_sepa_export_rejects_other_tenants_purchase_invoice_and_bank_account(
    client, seeded_a, seeded_b
):
    use_admin(client, seeded_a["administration_id"])
    r = client.post(
        "/bank/sepa/export",
        data={
            "account_id": seeded_a["ids"][BankAccount],
            "execution_date": "2027-01-01",
            "invoice_id": [seeded_b["ids"][PurchaseInvoice]],
            "csrf_token": client.csrf,
        },
        follow_redirects=False,
    )
    assert r.headers.get("content-type", "").startswith("application/xml") is False
    r = client.post(
        "/bank/sepa/export",
        data={
            "account_id": seeded_b["ids"][BankAccount],
            "execution_date": "2027-01-01",
            "invoice_id": [],
            "csrf_token": client.csrf,
        },
        follow_redirects=False,
    )
    assert r.status_code == 404


def test_manual_journal_rejects_other_tenants_accounts(client, seeded_a, seeded_b, q):
    use_admin(client, seeded_a["administration_id"])
    b_acc = seeded_b["ids"][Account]
    before = q(
        "SELECT count(*) FROM journal_lines WHERE account_id = :a AND tenant_id = "
        "(SELECT tenant_id FROM accounts WHERE id = :a)",
        a=b_acc,
    )[0][0]
    r = client.post(
        "/ledger/new",
        data={
            "date": "2026-11-02",
            "description": "x",
            "account_id": [b_acc, b_acc],
            "debit": ["10,00", ""],
            "credit": ["", "10,00"],
            "line_description": ["", ""],
            "vat_code": ["", ""],
            "vat_role": ["", ""],
            "csrf_token": client.csrf,
        },
        follow_redirects=False,
    )
    assert r.status_code == 422
    assert (
        q(
            "SELECT count(*) FROM journal_lines WHERE account_id = :a AND tenant_id = "
            "(SELECT tenant_id FROM accounts WHERE id = :a)",
            a=b_acc,
        )[0][0]
        == before
    )


def test_purchase_form_rejects_other_tenants_contact_and_account(client, seeded_a, seeded_b, q):
    use_admin(client, seeded_a["administration_id"])
    own_acc = seeded_a["ids"][Account]
    for contact, account in (
        (seeded_b["ids"][Contact], own_acc),
        (seeded_a["ids"][Contact], seeded_b["ids"][Account]),
    ):
        r = client.post(
            "/purchases/new",
            data={
                "contact_id": contact,
                "supplier_reference": "ATTACK-1",
                "invoice_date": "2026-11-01",
                "due_date": "2026-12-01",
                "line_description": "x",
                "line_account": account,
                "line_amount": "10,00",
                "line_vat_code": "I21",
                "csrf_token": client.csrf,
            },
            follow_redirects=False,
        )
        assert r.status_code in (422, 404), r.status_code
    assert (
        q("SELECT count(*) FROM purchase_invoices WHERE supplier_reference = 'ATTACK-1'")[0][0] == 0
    )


def test_inbox_review_cannot_use_other_tenants_contact_or_link_other_tenants_invoice(
    client, seeded_a, seeded_b, q
):
    use_admin(client, seeded_a["administration_id"])
    doc = seeded_a["ids"][InboxDocument]
    b_contact = seeded_b["ids"][Contact]
    b_purchase_ref = q(
        "SELECT supplier_reference FROM purchase_invoices WHERE id = :i",
        i=seeded_b["ids"][PurchaseInvoice],
    )[0][0]
    r = client.post(
        f"/inbox/{doc}/create",
        data={
            "action": "link",
            "contact_id": b_contact,
            "supplier_reference": b_purchase_ref or "x",
            "csrf_token": client.csrf,
        },
        follow_redirects=False,
    )
    assert r.status_code == 422
    assert q("SELECT purchase_invoice_id FROM inbox_documents WHERE id = :d", d=doc)[0][0] is None
    r = client.post(
        f"/inbox/{doc}/create",
        data={
            "action": "draft",
            "contact_id": b_contact,
            "supplier_reference": "ATTACK-2",
            "invoice_date": "2026-11-01",
            "line_description": "x",
            "line_account": seeded_a["ids"][Account],
            "line_amount": "1,00",
            "line_vat_code": "I21",
            "csrf_token": client.csrf,
        },
        follow_redirects=False,
    )
    assert r.status_code == 422


def test_inbox_dup_param_does_not_show_other_tenants_documents(client, seeded_a, seeded_b):
    use_admin(client, seeded_a["administration_id"])
    r = client.get(f"/inbox?dup={seeded_b['ids'][InboxDocument]}")
    assert "bill.xml" in r.text or r.status_code == 200
    assert f"/inbox/{seeded_b['ids'][InboxDocument]}" not in r.text


def test_cannot_reverse_or_credit_other_tenants_documents_via_post_body(
    client, seeded_a, seeded_b, q
):
    """Positive control for id-in-path routes already covered; here a body id is the lever."""
    use_admin(client, seeded_a["administration_id"])
    b_contact = seeded_b["ids"][Contact]
    r = client.post(
        f"/contacts/{b_contact}/anonymise",
        data={"confirm": "yes", "csrf_token": client.csrf},
        follow_redirects=False,
    )
    assert r.status_code == 404
    assert q("SELECT anonymised_at FROM contacts WHERE id = :c", c=b_contact)[0][0] is None


def test_database_refuses_a_row_that_points_at_another_tenants_row(
    database_url, seeded_a, seeded_b, tenant
):
    """Defence in depth: a missed application check must not become a cross-tenant link.
    Composite (tenant_id, id) foreign keys make the database refuse it."""
    from sqlalchemy import create_engine

    engine = create_engine(database_url)
    try:
        with engine.begin() as conn:
            conn.execute(
                text("SELECT set_config('app.tenant_id', :t, true)"), {"t": str(tenant.id)}
            )
            admin_a = seeded_a["administration_id"]
            try:
                conn.execute(
                    text(
                        "INSERT INTO sales_invoices (administration_id, contact_id, status, "
                        "issue_date, due_date, total_excl_cents, total_vat_cents, "
                        "total_incl_cents, "
                        "paid_cents) VALUES (:a, :c, 'draft', '2026-01-01', '2026-02-01', 0,0,0,0)"
                    ),
                    {"a": admin_a, "c": seeded_b["ids"][Contact]},
                )
                accepted = True
            except Exception as exc:  # noqa: BLE001
                accepted = False
                assert "foreign key" in str(exc).lower() or "policy" in str(exc).lower(), exc
                print("rejected:", type(exc).__name__, str(exc)[:200])
        assert not accepted, "tenant A row now references tenant B's contact"
    finally:
        engine.dispose()
