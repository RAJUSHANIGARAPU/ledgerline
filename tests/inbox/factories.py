# ruff: noqa: E501
"""Synthetic documents for the inbox tests: UBL files, text-layer PDFs and emails."""

from decimal import Decimal
from email.message import EmailMessage
from io import BytesIO

from pypdf import PdfWriter

from ledgerline.money import from_cents, percent_of

SUPPLIER_IBAN = "NL02ABNA0123456789"
NS = (
    'xmlns="urn:oasis:names:specification:ubl:schema:xsd:{root}-2" '
    'xmlns:cac="urn:oasis:names:tc:ubl:schema:xsd:CommonAggregateComponents-2" '
    'xmlns:cbc="urn:oasis:names:tc:ubl:schema:xsd:CommonBasicComponents-2"'
)


def _amt(cents: int) -> str:
    return f"{from_cents(cents):.2f}"


def ubl(
    lines,
    *,
    number="INV-2026-17",
    credit=False,
    vat="NL853345678B01",
    kvk="87654321",
    iban=SUPPLIER_IBAN,
    issue="2026-11-03",
    due="2026-12-03",
    name="Bakker Software B.V.",
    currency="EUR",
) -> bytes:
    """lines: (description, amount_cents, category, percent); AE means reverse charge."""
    root = "CreditNote" if credit else "Invoice"
    line_tag = "CreditNoteLine" if credit else "InvoiceLine"
    qty_tag = "CreditedQuantity" if credit else "InvoicedQuantity"
    groups: dict[tuple[str, int], int] = {}
    for _, amount, category, percent in lines:
        groups[(category, percent)] = groups.get((category, percent), 0) + amount
    subtotals, vat_total = [], 0
    for (category, percent), base in groups.items():
        tax = 0 if category == "AE" else percent_of(base, Decimal(percent))
        vat_total += tax
        subtotals.append(
            f'<cac:TaxSubtotal><cbc:TaxableAmount currencyID="{currency}">{_amt(base)}'
            f'</cbc:TaxableAmount><cbc:TaxAmount currencyID="{currency}">{_amt(tax)}'
            f"</cbc:TaxAmount><cac:TaxCategory><cbc:ID>{category}</cbc:ID>"
            f"<cbc:Percent>{percent}</cbc:Percent><cac:TaxScheme><cbc:ID>VAT</cbc:ID>"
            "</cac:TaxScheme></cac:TaxCategory></cac:TaxSubtotal>"
        )
    excl = sum(amount for _, amount, _, _ in lines)
    body = "".join(
        f'<cac:{line_tag}><cbc:ID>{i}</cbc:ID><cbc:{qty_tag} unitCode="C62">1</cbc:{qty_tag}>'
        f'<cbc:LineExtensionAmount currencyID="{currency}">{_amt(amount)}'
        f"</cbc:LineExtensionAmount><cac:Item><cbc:Name>{desc}</cbc:Name>"
        f"<cac:ClassifiedTaxCategory><cbc:ID>{category}</cbc:ID><cbc:Percent>{percent}"
        "</cbc:Percent><cac:TaxScheme><cbc:ID>VAT</cbc:ID></cac:TaxScheme>"
        f"</cac:ClassifiedTaxCategory></cac:Item></cac:{line_tag}>"
        for i, (desc, amount, category, percent) in enumerate(lines, 1)
    )
    xml = f"""<?xml version="1.0" encoding="UTF-8"?>
<{root} {NS.format(root=root)}>
<cbc:CustomizationID>urn:cen.eu:en16931:2017#compliant#urn:fdc:peppol.eu:2017:poacc:billing:3.0</cbc:CustomizationID>
<cbc:ID>{number}</cbc:ID><cbc:IssueDate>{issue}</cbc:IssueDate>{
        f"<cbc:DueDate>{due}</cbc:DueDate>" if due and not credit else ""
    }
<cbc:DocumentCurrencyCode>{currency}</cbc:DocumentCurrencyCode>
<cac:AccountingSupplierParty><cac:Party>
<cac:PartyName><cbc:Name>{name}</cbc:Name></cac:PartyName>
<cac:PartyTaxScheme><cbc:CompanyID>{
        vat
    }</cbc:CompanyID><cac:TaxScheme><cbc:ID>VAT</cbc:ID></cac:TaxScheme></cac:PartyTaxScheme>
<cac:PartyLegalEntity><cbc:RegistrationName>{
        name
    }</cbc:RegistrationName><cbc:CompanyID schemeID="0106">{
        kvk
    }</cbc:CompanyID></cac:PartyLegalEntity>
</cac:Party></cac:AccountingSupplierParty>
<cac:PaymentMeans><cbc:PaymentMeansCode>58</cbc:PaymentMeansCode><cac:PayeeFinancialAccount><cbc:ID>{
        iban
    }</cbc:ID></cac:PayeeFinancialAccount></cac:PaymentMeans>
<cac:TaxTotal><cbc:TaxAmount currencyID="{currency}">{_amt(vat_total)}</cbc:TaxAmount>{
        "".join(subtotals)
    }</cac:TaxTotal>
<cac:LegalMonetaryTotal><cbc:LineExtensionAmount currencyID="{currency}">{
        _amt(excl)
    }</cbc:LineExtensionAmount>
<cbc:TaxExclusiveAmount currencyID="{currency}">{_amt(excl)}</cbc:TaxExclusiveAmount>
<cbc:TaxInclusiveAmount currencyID="{currency}">{_amt(excl + vat_total)}</cbc:TaxInclusiveAmount>
<cbc:PayableAmount currencyID="{currency}">{
        _amt(excl + vat_total)
    }</cbc:PayableAmount></cac:LegalMonetaryTotal>
{body}
</{root}>"""
    return xml.encode()


def invoice_html(
    *,
    supplier="Bakker Software B.V.",
    number="2026-0042",
    date_text="3 november 2026",
    due_text="03-12-2026",
    excl="1.000,00",
    vat_rows=(("21", "1.000,00", "210,00"),),
    incl="1.210,00",
    extra="",
    vat="NL853345678B01",
    kvk="87654321",
    iban="NL02 ABNA 0123 4567 89",
    buyer_vat="NL123456789B01",
) -> str:
    vat_html = "".join(
        f"<tr><td>BTW {rate}% over € {base}</td><td>€ {amount}</td></tr>"
        for rate, base, amount in vat_rows
    )
    return f"""<html><body style="font-family: DejaVu Sans, sans-serif">
<h2>{supplier}</h2><p>Havenstraat 9, 3011 AB Rotterdam</p>
<p>KvK {kvk} - BTW-nummer {vat}</p><p>IBAN {iban}</p>
<h1>Factuur</h1>
<p>Factuurnummer: {number}</p><p>Factuurdatum: {date_text}</p><p>Vervaldatum: {due_text}</p>
<p>Klant: Testbedrijf, BTW {buyer_vat}</p>
<table><tr><td>Consultancy</td><td>€ {excl}</td></tr></table>
<table><tr><td>Subtotaal</td><td>€ {excl}</td></tr>{vat_html}
<tr><td>Totaal te betalen</td><td>€ {incl}</td></tr></table>{extra}
</body></html>"""


def pdf_from_html(html: str) -> bytes:
    from weasyprint import HTML

    return HTML(string=html).write_pdf()


def pdf_without_text() -> bytes:
    writer = PdfWriter()
    writer.add_blank_page(width=300, height=300)
    out = BytesIO()
    writer.write(out)
    return out.getvalue()


def eml(
    *,
    sender="boekhouder@leverancier.example",
    to="invoices@jouwbedrijf.example",
    subject="Factuur 2026-0042",
    attachments=(),
    message_id="<m1@leverancier.example>",
    body="Zie bijlage.",
    extra_headers=None,
) -> bytes:
    msg = EmailMessage()
    msg["From"] = sender
    msg["To"] = to
    msg["Subject"] = subject
    msg["Message-ID"] = message_id
    for key, value in (extra_headers or {}).items():
        msg[key] = value
    msg.set_content(body)
    for filename, content, maintype, subtype in attachments:
        msg.add_attachment(content, maintype=maintype, subtype=subtype, filename=filename)
    return msg.as_bytes()


def jpeg_bytes() -> bytes:
    return b"\xff\xd8\xff\xe0" + b"\x00" * 64


def _eur_nl(cents: int, space: bool = False) -> str:
    euros, rest = divmod(abs(cents), 100)
    sep = " " if space else "."
    body = f"{euros:,}".replace(",", sep)
    return f"{'-' if cents < 0 else ''}{body},{rest:02d}"


def _rows(rows: list[tuple[str, str]]) -> str:
    return "".join(f"<p><span>{a}</span> <span>{b}</span></p>" for a, b in rows)


def uber_statement_html(
    *, start="5/4/26", end="5/10/26", sales=40250, orders=14, fee=6030, fee_vat=1266,
    tips=0, adjustments=(("Promoties voor items", -2000),), payout=None, days=None,
) -> str:
    """A synthetic Uber Eats weekly overview (fake restaurant, fake amounts)."""
    net = sales - fee - fee_vat
    total = net + tips + sum(c for _, c in adjustments) if payout is None else payout
    rows = [("Totaal", f"{orders} {_eur_nl(sales, True)} €"),
            ("Uber Eats-servicekosten", f"{_eur_nl(-fee, True)} €"),
            ("Btw op servicekosten", f"{_eur_nl(-fee_vat, True)} €"),
            ("Netto verkopen", f"{_eur_nl(net, True)} €")]
    if tips:
        rows.append(("Fooi", f"{_eur_nl(tips, True)} €"))
    rows += [(label, f"{_eur_nl(c, True)} €") for label, c in adjustments]
    rows.append(("Totale betaling", f"{_eur_nl(total, True)} €"))
    daily = days if days is not None else [("5/5/26", orders, sales)]
    day_rows = [(d, f"{n} {_eur_nl(c, True)} €") for d, n, c in daily]
    return (f"<h1>Test Bistro Voorbeeld</h1><p>Betaaloverzicht voor {start} - {end}</p>"
            f"<p>Hi Test Bistro Voorbeeld ,</p>{_rows(day_rows)}{_rows(rows)}")


def thuisbezorgd_statement_html(
    *, start="01-03-2026", end="07-03-2026", total=100000, cash=4000, tips=600, refund=0,
    commission=14000, extras=(("TopRank: 10 bestellingen", 2500),), admin=700, payout=None,
    invoice_no="900000001",
) -> str:
    """A synthetic Thuisbezorgd invoice plus overview (fake restaurant, fake amounts)."""
    online = total - cash
    fee_lines = [(f"Kosten: 14,00% van € {_eur_nl(total)}", commission),
                 *[(label, cents) for label, cents in extras], ("Kosten: € 0,21 x 33", admin)]
    subtotal = sum(c for _, c in fee_lines)
    vat = round(subtotal * 21 / 100)
    invoice = subtotal + vat
    outstanding = online + tips + refund
    paid = outstanding - invoice if payout is None else payout
    intro = f"Thuisbezorgd.nl ({start} t/m {end}): 33 bestellingen t.w.v. € {_eur_nl(total)}"
    spec = [("Totaal", f"33 bestellingen t.w.v. € {_eur_nl(total)}")]
    if cash:
        spec.append(("Betaald bij bezorging", f"2 bestellingen t.w.v. € {_eur_nl(cash)}"))
    spec += [("Online betaald *", f"31 bestellingen t.w.v. € {_eur_nl(online)}")]
    if tips:
        spec.append(("Fooien", f"5 bestellingen t.w.v. € {_eur_nl(tips)}"))
    if refund:
        spec.append(("terugboeking", f"1 bestellingen t.w.v. € {_eur_nl(refund)}"))
    return (
        f"<h1>Factuur</h1><p>Test Bistro Voorbeeld</p><p>Datum: 08-03-2026 "
        f"Factuurnummer: {invoice_no}</p><p>De volgende diensten brengen wij u in rekening:</p>"
        f"<p>{intro}</p>{_rows([(a, f'€ {_eur_nl(b)}') for a, b in fee_lines])}"
        f"{_rows([('Subtotaal', f'€ {_eur_nl(subtotal)}')])}"
        f"{_rows([(f'BTW (21% over € {_eur_nl(subtotal)})', f'€ {_eur_nl(vat)}')])}"
        f"{_rows([('Totaalbedrag van deze factuur', f'€ {_eur_nl(invoice)}')])}"
        "<h1>Specificaties</h1>"
        f"<p>Periode: {start} t/m {end}</p>{_rows(spec)}"
        f"{_rows([('Uitstaand saldo online betalingen op 08-03-2026 **', f'€ {_eur_nl(outstanding)}'), ('Verrekening van factuur ' + invoice_no, f'€ {_eur_nl(invoice)}'), ('Uitbetaling naar rekening NL00TEST0000000000 t.n.v. Test Bistro ', f'€ {_eur_nl(paid)}')])}"
    )
