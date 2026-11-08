"""Pages of tenant A must not change when tenant B's books grow (dashboard, reports, lists)."""

import re
from datetime import date

from sqlalchemy import text

from ledgerline.models import Contact, SalesInvoice
from tests.adversarial.conftest import use_admin
from tests.conftest import tenant_db
from tests.tenancy.test_routes import all_routes

PAGES = [
    p
    for p, m in all_routes()
    if m == "GET" and "{" not in p and p != "/logout" and not p.endswith(".zip")
]


def _norm(body: str) -> str:
    return re.sub(r'(csrf_token" value=")[^"]+', r"\1X", body)


def test_pages_of_a_do_not_change_when_b_grows(client, seeded_a, seeded_b, owner_engine):
    use_admin(client, seeded_a["administration_id"])
    urls = PAGES + ["/reports/aging/sales?export=csv", "/ledger?q=%25"]
    before = {u: client.get(u, follow_redirects=False) for u in urls}
    with owner_engine.connect() as conn:
        b_tenant = conn.execute(
            text("SELECT tenant_id FROM administrations WHERE id = :a"),
            {"a": seeded_b["administration_id"]},
        ).scalar_one()
    with tenant_db(b_tenant) as s:
        for i in range(7):
            s.add(
                SalesInvoice(
                    administration_id=seeded_b["administration_id"],
                    contact_id=seeded_b["ids"][Contact],
                    status="open",
                    number=f"ZZ-{i}",
                    issue_date=date.today(),
                    due_date=date.today(),
                    total_excl_cents=987654321,
                    total_vat_cents=0,
                    total_incl_cents=987654321,
                    paid_cents=0,
                )
            )
        s.commit()
    changed = []
    for u in urls:
        after = client.get(u, follow_redirects=False)
        if (after.status_code, _norm(after.text)) != (before[u].status_code, _norm(before[u].text)):
            changed.append(u)
        assert "9.876.543" not in after.text and "9876543" not in after.text, u
    assert not changed, changed
