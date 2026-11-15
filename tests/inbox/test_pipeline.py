import pytest
from sqlalchemy import select

from ledgerline.config import get_settings
from ledgerline.inbox import service
from ledgerline.inbox.extract import ExtractedInvoice, ExtractedLine, ExtractionError, pipeline
from ledgerline.models import InboxDocument, InboxStatus
from tests.inbox.factories import invoice_html, pdf_from_html, pdf_without_text, ubl


def receive(session, admin, content, name="doc", **kwargs):
    document, _ = service.receive(session, admin.id, name, content, "upload", **kwargs)
    session.commit()
    return document


def test_ubl_document_is_read_exactly(session, admin):
    document = receive(session, admin, ubl([("Hosting", 10000, "S", 21)]), "inv.xml")
    pipeline.run(session, document)
    assert document.status == InboxStatus.REVIEW and document.extraction_method == "ubl"
    assert document.extraction_error is None and document.attempts == 1
    assert document.extraction["total_incl_cents"] == 12100
    assert document.extraction["invoice_date"] == "2026-11-03"


def test_pdf_with_text_uses_pdf_text(session, admin):
    document = receive(session, admin, pdf_from_html(invoice_html()), "inv.pdf")
    pipeline.run(session, document)
    assert document.extraction_method == "pdf_text"
    assert document.extraction["invoice_number"] == "2026-0042"


def test_pdf_with_own_vat_number_ignores_it(session, admin):
    html = invoice_html(vat="NL853345678B01", buyer_vat=admin.vat_number)
    document = receive(session, admin, pdf_from_html(html), "inv.pdf")
    pipeline.run(session, document)
    assert document.extraction["supplier_vat_number"] == "NL853345678B01"


def test_no_text_layer_without_vision_is_marked_for_manual_review(session, admin):
    document = receive(session, admin, pdf_without_text(), "scan.pdf")
    pipeline.run(session, document)
    assert document.status == InboxStatus.REVIEW
    assert document.extraction is None and document.extraction_method == "none"
    assert "no text layer" in document.extraction_error


def test_broken_xml_never_raises(session, admin):
    document = receive(session, admin, b"<?xml version='1.0'?><Invoice><a>", "bad.xml")
    pipeline.run(session, document)
    assert document.status == InboxStatus.REVIEW and document.extraction is None
    assert document.extraction_error


class FakeVision:
    method = "vision"

    def __init__(self, result=None, error=None):
        self.result, self.error, self.calls = result, error, 0

    def can_handle(self, content_type, content):
        return content_type in ("application/pdf", "image/jpeg", "image/png")

    def extract(self, content_type, content):
        self.calls += 1
        if self.error:
            raise self.error
        return self.result


@pytest.fixture
def vision_on(monkeypatch):
    monkeypatch.setenv("LEDGERLINE_EXTRACTION_PROVIDER", "anthropic")
    get_settings.cache_clear()
    yield
    monkeypatch.undo()
    get_settings.cache_clear()


def install_vision(monkeypatch, fake):
    import sys
    import types

    module = types.ModuleType("ledgerline.inbox.extract.vision")
    module.get_extractor = lambda session, administration_id: fake
    monkeypatch.setitem(sys.modules, "ledgerline.inbox.extract.vision", module)


def test_vision_is_the_last_resort_and_only_after_pdf_text_fails(
    session, admin, monkeypatch, vision_on
):
    fake = FakeVision(
        ExtractedInvoice(
            invoice_number="V-1", total_incl_cents=500, lines=[ExtractedLine("x", 500, 0, 0)]
        )
    )
    install_vision(monkeypatch, fake)
    scan = receive(session, admin, pdf_without_text(), "scan.pdf")
    pipeline.run(session, scan)
    assert scan.extraction_method == "vision" and scan.extraction["invoice_number"] == "V-1"
    text = receive(session, admin, pdf_from_html(invoice_html()), "text.pdf")
    pipeline.run(session, text)
    assert text.extraction_method == "pdf_text" and fake.calls == 1


def test_vision_failure_falls_back_to_manual(session, admin, monkeypatch, vision_on):
    install_vision(monkeypatch, FakeVision(error=ExtractionError("model refused")))
    document = receive(session, admin, pdf_without_text(), "scan.pdf")
    pipeline.run(session, document)
    assert document.extraction_method == "none"
    assert (
        "no text layer" in document.extraction_error
        and "model refused" in document.extraction_error
    )


def test_vision_unexpected_crash_is_contained(session, admin, monkeypatch, vision_on):
    install_vision(monkeypatch, FakeVision(error=RuntimeError("boom with secrets")))
    document = receive(session, admin, pdf_without_text(), "scan.pdf")
    pipeline.run(session, document)
    assert "boom" not in document.extraction_error
    assert "unexpected error" in document.extraction_error


def test_vision_is_skipped_when_the_provider_is_none(session, admin, monkeypatch):
    fake = FakeVision(ExtractedInvoice(invoice_number="V-1"))
    install_vision(monkeypatch, fake)
    document = receive(session, admin, pdf_without_text(), "scan.pdf")
    pipeline.run(session, document)
    assert fake.calls == 0 and document.extraction_method == "none"


def test_attempts_accumulate_on_rerun(session, admin):
    document = receive(session, admin, pdf_without_text(), "scan.pdf")
    pipeline.run(session, document)
    pipeline.run(session, document)
    assert document.attempts == 2


def test_ubl_next_to_pdf_in_the_same_email_is_preferred(session, admin):
    pdf = receive(
        session,
        admin,
        pdf_from_html(invoice_html(number="PDF-ONLY")),
        "inv.pdf",
        email_message_id="<same@x>",
        sender="a@b.example",
    )
    xml = receive(
        session,
        admin,
        ubl([("Hosting", 10000, "S", 21)], number="UBL-1"),
        "inv.xml",
        email_message_id="<same@x>",
        sender="a@b.example",
    )
    for doc in (pdf, xml):
        doc.source = "email"
    session.commit()
    pipeline.run(session, pdf)
    session.commit()
    assert pdf.extraction_method == "ubl" and pdf.extraction["invoice_number"] == "UBL-1"
    session.refresh(xml)
    assert xml.status == InboxStatus.REJECTED and "Same invoice" in xml.extraction_error
    assert (
        session.scalars(select(InboxDocument).where(InboxDocument.status == InboxStatus.REVIEW))
        .one()
        .id
        == pdf.id
    )


PAYSLIPS = [
    "<h1>Salarisspecificatie</h1><p>Periode 07</p><p>Netto 1.234,56</p>",
    "<p>Bakker Horeca</p><p>Maandoverzicht</p><p>Bruto loon 2.000,00</p>"
    "<p>Loonheffing 400,00</p><p>Netto loon 1.600,00</p>",
    "<p>Naam medewerker</p>" + "<p>regel</p>" * 12 + "<p>Loonstrook juli</p><p>Netto 1.234,56</p>",
]


@pytest.mark.parametrize("html", PAYSLIPS)
@pytest.mark.parametrize("provider", ["anthropic", "openrouter"])
def test_a_payslip_never_reaches_the_vision_step(
    session, admin, monkeypatch, html, provider
):
    import sys
    import types

    monkeypatch.setenv("LEDGERLINE_EXTRACTION_PROVIDER", provider)
    get_settings.cache_clear()
    fake = FakeVision(ExtractedInvoice(invoice_number="V-1", total_incl_cents=500))
    name = "vision" if provider == "anthropic" else provider
    module = types.ModuleType(f"ledgerline.inbox.extract.{name}")
    module.get_extractor = lambda session, administration_id: fake
    monkeypatch.setitem(sys.modules, module.__name__, module)
    document = receive(session, admin, pdf_from_html(html), "loon.pdf")
    pipeline.run(session, document)
    get_settings.cache_clear()
    assert fake.calls == 0
    assert document.extraction["document_type"] == "payslip"
    assert document.extraction_method == "pdf_text"


@pytest.fixture
def capped_vision(session, admin, monkeypatch):
    settings = get_settings()
    monkeypatch.setattr(settings, "extraction_provider", "anthropic")
    monkeypatch.setattr(settings, "extraction_monthly_limit", 1)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
    used = receive(session, admin, pdf_without_text() + b"\n% used", "used.pdf")
    used.extraction_method = "vision"
    session.commit()


def test_a_document_over_the_monthly_cap_says_why(session, admin, capped_vision):
    document = receive(session, admin, pdf_without_text(), "scan.pdf")
    pipeline.run(session, document)
    assert document.extraction is None and document.extraction_method == "limit"
    assert "limit of 1 documents" in document.extraction_error
    assert "resets next month" in document.extraction_error


def test_a_text_pdf_is_still_read_over_the_cap(session, admin, capped_vision):
    document = receive(session, admin, pdf_from_html(invoice_html()), "text.pdf")
    pipeline.run(session, document)
    assert document.extraction_method == "pdf_text" and document.extraction_error is None


def test_the_cap_reason_is_not_given_when_photo_reading_is_off(session, admin):
    document = receive(session, admin, pdf_without_text(), "scan.pdf")
    pipeline.run(session, document)
    assert document.extraction_method == "none"
