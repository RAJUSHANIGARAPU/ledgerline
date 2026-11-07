"""Fictional demo data: a year of invoices, purchases and bank activity.

Every name, number and IBAN here is made up (the IBANs are published example numbers).
"""
from datetime import date, timedelta
from decimal import Decimal
from xml.sax.saxutils import escape

from sqlalchemy import select

from ledgerline.banking import service as banking
from ledgerline.invoicing import service as invoicing
from ledgerline.invoicing.service import PurchaseLineInput, SalesLineInput
from ledgerline.ledger.chart import create_administration
from ledgerline.models import Account, Administration, Contact, ContactKind
from ledgerline.money import from_cents

DEMO_NAME = "Studio Noordlicht (demo)"
DEMO_IBAN = "NL91ABNA0417164300"


class DemoExists(RuntimeError):
    pass


def _month_start(today: date, months_back: int) -> date:
    year, month = today.year, today.month - months_back
    while month < 1:
        month += 12
        year -= 1
    return date(year, month, 1)


def _camt(entries: list[dict], opening_cents: int) -> bytes:
    closing = opening_cents + sum(e["cents"] for e in entries)
    first = min(e["date"] for e in entries)
    last = max(e["date"] for e in entries)

    def bal(code: str, cents: int, when: date) -> str:
        ind = "CRDT" if cents >= 0 else "DBIT"
        return (f"<Bal><Tp><CdOrPrtry><Cd>{code}</Cd></CdOrPrtry></Tp>"
                f'<Amt Ccy="EUR">{from_cents(abs(cents))}</Amt><CdtDbtInd>{ind}</CdtDbtInd>'
                f"<Dt><Dt>{when.isoformat()}</Dt></Dt></Bal>")

    rows = []
    for i, e in enumerate(entries, 1):
        incoming = e["cents"] > 0
        party = "Dbtr" if incoming else "Cdtr"
        rows.append(
            f'<Ntry><Amt Ccy="EUR">{from_cents(abs(e["cents"]))}</Amt>'
            f"<CdtDbtInd>{'CRDT' if incoming else 'DBIT'}</CdtDbtInd><Sts>BOOK</Sts>"
            f"<BookgDt><Dt>{e['date'].isoformat()}</Dt></BookgDt>"
            f"<ValDt><Dt>{e['date'].isoformat()}</Dt></ValDt>"
            f"<AcctSvcrRef>DEMO-{i:04d}</AcctSvcrRef><NtryDtls><TxDtls>"
            f"<Refs><EndToEndId>NOTPROVIDED</EndToEndId></Refs><RltdPties>"
            f"<{party}><Nm>{escape(e['name'])}</Nm></{party}>"
            f"<{party}Acct><Id><IBAN>{e['iban']}</IBAN></Id></{party}Acct></RltdPties>"
            f"<RmtInf><Ustrd>{escape(e['text'])}</Ustrd></RmtInf></TxDtls></NtryDtls></Ntry>"
        )
    return (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<Document xmlns="urn:iso:std:iso:20022:tech:xsd:camt.053.001.02"><BkToCstmrStmt>'
        "<GrpHdr><MsgId>DEMO</MsgId><CreDtTm>2026-01-01T00:00:00</CreDtTm></GrpHdr>"
        f"<Stmt><Id>DEMO-STMT</Id><Acct><Id><IBAN>{DEMO_IBAN}</IBAN></Id><Ccy>EUR</Ccy></Acct>"
        f"{bal('OPBD', opening_cents, first)}{bal('CLBD', closing, last)}"
        f"{''.join(rows)}</Stmt></BkToCstmrStmt></Document>"
    ).encode()


def seed(session, today: date | None = None) -> Administration:
    today = today or date.today()
    if session.scalar(select(Administration).where(Administration.name == DEMO_NAME)):
        raise DemoExists("demo data is already loaded")

    admin = create_administration(
        session, name=DEMO_NAME, legal_form="eenmanszaak", kvk_number="00000000",
        vat_number="NL000099998B57", iban=DEMO_IBAN, address_line="Voorbeeldkade 12",
        postcode="1234 AB", city="Utrecht", email="hallo@noordlicht.example", invoice_prefix="NL",
    )

    def acc(code: str) -> int:
        return session.scalar(
            select(Account.id).where(Account.administration_id == admin.id, Account.code == code)
        )

    def contact(name, kind, **fields) -> Contact:
        c = Contact(administration_id=admin.id, name=name, kind=kind, country="NL", **fields)
        session.add(c)
        session.flush()
        return c

    customers = [
        contact("Bakkerij De Zonnebloem", ContactKind.CUSTOMER, email="info@zonnebloem.example",
                address_line="Marktplein 3", postcode="3511 AA", city="Utrecht",
                iban="NL02ABNA0123456789"),
        contact("Fietsenmaker Van Dijk", ContactKind.CUSTOMER, address_line="Spoorlaan 8",
                postcode="5038 CB", city="Tilburg"),
        contact("Atelier Lumen BVBA", ContactKind.CUSTOMER, address_line="Kerkstraat 5",
                postcode="2000", city="Antwerpen", vat_number="BE0123456749"),
    ]
    customers[2].country = "BE"
    suppliers = {
        "software": contact("Cloudhuis Hosting", ContactKind.SUPPLIER, iban="NL86INGB0002445588",
                            address_line="Serverweg 1", postcode="1000 AA", city="Amsterdam"),
        "phone": contact("Belbundel Mobiel", ContactKind.SUPPLIER,
                         address_line="Antennestraat 2", postcode="2500 AA", city="Den Haag"),
        "books": contact("Boekhandel Het Leesteken", ContactKind.SUPPLIER,
                         address_line="Boekenrij 9", postcode="9711 AA", city="Groningen"),
    }

    bank_entries: list[dict] = []
    for back in range(11, -1, -1):
        month = _month_start(today, back)
        for n, (customer, hours, rate) in enumerate(
            [(customers[0], 12 + back % 4, 8500), (customers[1], 6 + back % 3, 8500)]
        ):
            issue = month + timedelta(days=3 + 10 * n)
            if issue > today:
                continue
            invoice = invoicing.create_sales_invoice(
                session, admin, customer,
                [SalesLineInput("Design and development, hours", Decimal(hours), rate, "V21",
                                acc("8000"))],
                issue_date=issue,
            )
            invoicing.issue_sales_invoice(session, admin, invoice, None)
            paid_on = issue + timedelta(days=12 + 6 * n)
            if back >= 1 and paid_on < today:
                bank_entries.append({
                    "date": paid_on, "cents": invoice.total_incl_cents, "name": customer.name,
                    "iban": customer.iban or "NL02ABNA0123456789",
                    "text": f"Factuur {invoice.number}",
                })

        bill_day = month + timedelta(days=1)
        if bill_day <= today:
            for key, account, desc, cents, code in (
                ("software", "4300", "Hosting and domain", 2900, "I21"),
                ("phone", "4400", "Mobile subscription", 2479, "I21"),
            ):
                bill = invoicing.create_purchase_invoice(
                    session, admin, suppliers[key],
                    [PurchaseLineInput(desc, acc(account), cents, code)],
                    supplier_reference=f"{key.upper()}-{month:%Y%m}", invoice_date=bill_day,
                )
                invoicing.book_purchase_invoice(session, admin, bill, None)
                if back >= 1:
                    bank_entries.append({
                        "date": bill_day + timedelta(days=5), "cents": -bill.total_incl_cents,
                        "name": suppliers[key].name, "iban": "NL86INGB0002445588",
                        "text": f"{suppliers[key].name} {bill.supplier_reference}",
                    })

    eu_month = _month_start(today, 2)
    eu_invoice = invoicing.create_sales_invoice(
        session, admin, customers[2],
        [SalesLineInput("Brand identity workshop", Decimal(1), 180000, "VEU", acc("8030"))],
        issue_date=eu_month + timedelta(days=20),
    )
    invoicing.issue_sales_invoice(session, admin, eu_invoice, None)
    books = invoicing.create_purchase_invoice(
        session, admin, suppliers["books"],
        [PurchaseLineInput("Reference books on typography", acc("4500"), 8450, "I9")],
        supplier_reference="LT-2041", invoice_date=_month_start(today, 4) + timedelta(days=14),
    )
    invoicing.book_purchase_invoice(session, admin, books, None)
    invoicing.create_sales_invoice(
        session, admin, customers[0],
        [SalesLineInput("Menu card redesign (draft)", Decimal(1), 45000, "V21", acc("8000"))],
        issue_date=today,
    )

    last_month = _month_start(today, 1)
    bank_entries += [
        {"date": last_month + timedelta(days=27), "cents": -1250, "name": "Demo Bank",
         "iban": DEMO_IBAN, "text": "Kosten betaalrekening"},
        {"date": last_month + timedelta(days=15), "cents": -6495, "name": "Kantoorwinkel Pen & Co",
         "iban": "NL86INGB0002445588", "text": "Bonnummer 5521"},
    ]
    bank_entries.sort(key=lambda e: e["date"])
    bank_account = banking.create_bank_account(session, admin, DEMO_IBAN, "Business account")
    banking.import_statement(session, bank_account, "demo-statement.xml",
                             _camt(bank_entries, 250000))
    banking.accept_exact_matches(session, bank_account)
    return admin
