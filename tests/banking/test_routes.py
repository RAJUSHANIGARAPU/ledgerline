from sqlalchemy import select

from ledgerline.banking import service
from ledgerline.ledger.chart import create_administration
from ledgerline.models import BankTransaction, InvoiceStatus, TxStatus
from tests.helpers import account, contact

from .conftest import fixture_bytes, sales_invoice

SUPPLIER_IBAN = "NL86INGB0002445588"


def upload(client, bank, name="camt053_02.xml"):
    return client.post(
        f"/bank/accounts/{bank.id}/import",
        files={"file": (name, fixture_bytes(name), "application/xml")},
        data={"csrf_token": client.csrf},
        follow_redirects=False,
    )


def post(client, url, **data):
    return client.post(url, data={"csrf_token": client.csrf, **data}, follow_redirects=False)


def test_pages_render_for_owner(client, bank):
    assert "Zakelijk" in client.get("/bank").text
    assert client.get("/bank/sepa").status_code == 200
    assert client.get(f"/bank/reconcile?account_id={bank.id}").status_code == 200


def test_upload_then_match_through_the_ui(client, session, admin, bank):
    klant = contact(session, admin)
    invoice = sales_invoice(session, admin, klant, "2026-0001", 12_100)
    session.commit()
    assert upload(client, bank).status_code == 303
    page = client.get(f"/bank/reconcile?account_id={bank.id}")
    assert "invoice number 2026-0001 in description" in page.text
    tx = session.scalar(select(BankTransaction).where(BankTransaction.amount_cents == 12_100))
    response = post(
        client, f"/bank/transactions/{tx.id}/match", allocation=f"sales:{invoice.id}:12100"
    )
    assert response.status_code == 303
    session.expire_all()
    assert session.get(type(invoice), invoice.id).status == InvoiceStatus.PAID
    assert session.get(BankTransaction, tx.id).status == TxStatus.MATCHED
    post(client, f"/bank/transactions/{tx.id}/undo")
    session.expire_all()
    assert session.get(BankTransaction, tx.id).status == TxStatus.UNMATCHED


def test_upload_of_the_same_file_flashes_an_error(client, bank):
    upload(client, bank)
    client.get("/bank")
    upload(client, bank)
    assert "already been imported" in client.get("/bank").text


def test_malicious_upload_is_refused_without_importing(client, session, bank):
    response = client.post(
        f"/bank/accounts/{bank.id}/import",
        files={"file": ("x.xml", fixture_bytes("billion_laughs.xml"), "application/xml")},
        data={"csrf_token": client.csrf},
        follow_redirects=False,
    )
    assert response.status_code == 303
    assert "safe XML" in client.get("/bank").text
    assert session.scalar(select(BankTransaction)) is None


def test_book_to_account_through_the_ui(client, session, admin, bank):
    upload(client, bank)
    tx = session.scalar(select(BankTransaction).where(BankTransaction.amount_cents == -5_000))
    response = post(
        client, f"/bank/transactions/{tx.id}/book",
        account_id=account(session, admin, "4200").id, vat_code="I21",
    )
    assert response.status_code == 303
    session.expire_all()
    assert session.get(BankTransaction, tx.id).status == TxStatus.MATCHED


def test_sepa_download_is_an_attachment(client, session, admin, bank):
    from .conftest import purchase_invoice

    supplier = contact(session, admin, name="Leverancier BV", iban=SUPPLIER_IBAN)
    invoice = purchase_invoice(session, admin, supplier, "INK-1", 5_000)
    session.commit()
    response = client.post(
        "/bank/sepa/export",
        data={"csrf_token": client.csrf, "account_id": bank.id,
              "execution_date": "2026-06-20", "invoice_id": invoice.id},
    )
    assert response.status_code == 200
    assert "attachment" in response.headers["content-disposition"]
    assert b"<CtrlSum>50.00</CtrlSum>" in response.content


def other_admin_transaction(session):
    other = create_administration(session, name="Andere BV")
    session.commit()
    account_ = service.create_bank_account(session, other, "NL02ABNA0123456789", "Ander")
    session.commit()
    service.import_statement(session, account_, "x.xml", fixture_bytes("camt053_02.xml").replace(
        b"NL91ABNA0417164300", b"NL02ABNA0123456789"))
    session.commit()
    return account_, session.scalar(
        select(BankTransaction).where(BankTransaction.bank_account_id == account_.id)
    )


def test_other_administrations_data_is_404(client, session, bank):
    foreign_account, foreign_tx = other_admin_transaction(session)
    for action in ("ignore", "undo", "match"):
        assert post(client, f"/bank/transactions/{foreign_tx.id}/{action}").status_code == 404
    assert post(client, f"/bank/transactions/{foreign_tx.id}/book", account_id=1).status_code == 404
    assert client.get(f"/bank/reconcile?account_id={foreign_account.id}").status_code == 404
    assert post(client, f"/bank/accounts/{foreign_account.id}/accept-exact").status_code == 404
    assert upload(client, foreign_account).status_code == 404
    session.expire_all()
    assert session.get(BankTransaction, foreign_tx.id).status == TxStatus.UNMATCHED


def test_readonly_user_cannot_write(readonly_client, session, bank):
    upload(readonly_client, bank)
    assert upload(readonly_client, bank).status_code == 403
    for url in (
        f"/bank/accounts/{bank.id}/accept-exact",
        "/bank/transactions/1/ignore",
        "/bank/transactions/1/undo",
        "/bank/sepa/export",
    ):
        assert post(readonly_client, url).status_code == 403
    assert post(readonly_client, "/bank/accounts", iban=SUPPLIER_IBAN, name="x").status_code == 403
    assert readonly_client.get("/bank").status_code == 200


def test_post_without_csrf_is_refused(client, bank):
    response = client.post("/bank/accounts", data={"iban": SUPPLIER_IBAN, "name": "x"})
    assert response.status_code == 403
