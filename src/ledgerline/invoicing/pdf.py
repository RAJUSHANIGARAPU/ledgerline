"""Invoice PDF: Jinja HTML rendered by WeasyPrint with all network fetching disabled."""
from ledgerline.invoicing.calc import vat_by_code
from ledgerline.models import Administration, SalesInvoice
from ledgerline.vat import codes
from ledgerline.web import templates


def _no_fetch(url: str, *args, **kwargs):
    raise ValueError(f"external resources are not allowed: {url}")


def vat_summary(invoice: SalesInvoice) -> list[dict]:
    per_code = vat_by_code((line.vat_code, line.line_excl_cents) for line in invoice.lines)
    return [
        {"code": code, "rate": codes.get(code).rate, "base": base, "vat": vat}
        for code, (base, vat) in per_code.items()
    ]


def invoice_notes(invoice: SalesInvoice) -> list[str]:
    seen: list[str] = []
    for line in invoice.lines:
        note = codes.get(line.vat_code).invoice_note
        if note and note not in seen:
            seen.append(note)
    return seen


def render_invoice_html(
    invoice: SalesInvoice, admin: Administration, original_number: str | None = None
) -> str:
    return templates.env.get_template("invoicing/invoice_pdf.html").render(
        invoice=invoice,
        admin=admin,
        original_number=original_number,
        is_credit=invoice.credit_note_of_id is not None,
        vat_summary=vat_summary(invoice),
        notes=invoice_notes(invoice),
        rates={line.vat_code: codes.get(line.vat_code).rate for line in invoice.lines},
    )


def render_invoice_pdf(
    invoice: SalesInvoice, admin: Administration, original_number: str | None = None
) -> bytes:
    from weasyprint import HTML

    html = render_invoice_html(invoice, admin, original_number)
    return HTML(string=html, url_fetcher=_no_fetch).write_pdf()
