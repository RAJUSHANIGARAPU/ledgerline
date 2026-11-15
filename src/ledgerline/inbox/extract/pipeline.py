"""Run the extractors over an inbox document, most trustworthy first; never raises on bad input."""
import logging

from sqlalchemy import select
from sqlalchemy.orm import Session

from ledgerline.config import get_settings
from ledgerline.inbox.extract import ExtractionError, Extractor
from ledgerline.inbox.extract.pdf_text import PdfTextExtractor
from ledgerline.inbox.extract.ubl import UblExtractor
from ledgerline.models import Administration, Attachment, InboxDocument, InboxStatus

log = logging.getLogger(__name__)
LIMIT_METHOD = "limit"
PHOTO_TYPES = {"image/jpeg", "image/png", "application/pdf"}
SIBLING_NOTE = "Same invoice as the PDF from this email; its data was read from this e-invoice."


def _vision_extractor(session: Session, administration_id: int) -> Extractor | None:
    provider = get_settings().extraction_provider
    try:
        if provider == "anthropic":
            from ledgerline.inbox.extract.vision import get_extractor
        elif provider == "openrouter":
            from ledgerline.inbox.extract.openrouter import get_extractor
        else:
            return None
    except ImportError:
        log.warning("vision extraction is enabled but not available")
        return None
    return get_extractor(session, administration_id)


def vision_limit_reached(session: Session, administration_id: int) -> tuple[int, int] | None:
    """(used, limit) when photo reading is configured and this month's cap is used up."""
    settings = get_settings()
    limit = settings.extraction_monthly_limit
    provider = settings.extraction_provider
    if limit <= 0 or provider not in ("anthropic", "openrouter"):
        return None
    try:
        if provider == "anthropic":
            from ledgerline.inbox.extract.vision import has_credentials
        else:
            from ledgerline.inbox.extract.openrouter import has_credentials
        from ledgerline.inbox.extract.vision import vision_usage_this_month
    except ImportError:
        return None
    if not has_credentials():
        return None
    used = vision_usage_this_month(session, administration_id)
    return (used, limit) if used >= limit else None


def limit_message(limit: int) -> str:
    return (f"The monthly photo-reading limit of {limit} documents was reached, so this one was "
            "not read. The limit resets next month; the operator can raise it with "
            "LEDGERLINE_EXTRACTION_MONTHLY_LIMIT. Enter the invoice by hand.")


def build_extractors(session: Session, admin: Administration) -> list[Extractor]:
    own = ([admin.vat_number], [admin.iban], [admin.kvk_number], [admin.name])
    extractors: list[Extractor] = [UblExtractor(), PdfTextExtractor(*own)]
    vision = _vision_extractor(session, admin.id)
    if vision is not None:
        extractors.append(vision)
    return extractors


def _sibling_ubl(session: Session, document: InboxDocument) -> tuple[InboxDocument, bytes] | None:
    if document.source != "email" or not document.email_message_id:
        return None
    rows = session.execute(
        select(InboxDocument, Attachment)
        .join(Attachment, Attachment.id == InboxDocument.attachment_id)
        .where(
            InboxDocument.administration_id == document.administration_id,
            InboxDocument.email_message_id == document.email_message_id,
            InboxDocument.id != document.id,
            Attachment.content_type == "application/xml",
        )
    ).all()
    for sibling, attachment in rows:
        if UblExtractor().can_handle("application/xml", attachment.content):
            return sibling, attachment.content
    return None


def run(session: Session, document: InboxDocument) -> None:
    attachment = session.get(Attachment, document.attachment_id)
    admin = session.get(Administration, document.administration_id)
    content, content_type = attachment.content, attachment.content_type
    errors: list[str] = []
    result, method, sibling = None, "none", None

    candidates: list[tuple[Extractor, str, bytes]] = []
    if content_type == "application/pdf":
        sibling = _sibling_ubl(session, document)
        if sibling is not None:
            candidates.append((UblExtractor(), "application/xml", sibling[1]))
    candidates += [(e, content_type, content) for e in build_extractors(session, admin)]
    used_sibling = False
    for extractor, ctype, data in candidates:
        try:
            if not extractor.can_handle(ctype, data):
                continue
            result = extractor.extract(ctype, data)
            method = extractor.method
            used_sibling = ctype != content_type
            break
        except ExtractionError as exc:
            errors.append(str(exc)[:200])
        except Exception:
            log.exception("extractor %s failed on document %s", extractor.method, document.id)
            errors.append("unexpected error while reading the document")
    if used_sibling and sibling[0].status in (InboxStatus.NEW, InboxStatus.REVIEW):
        sibling[0].status = InboxStatus.REJECTED
        sibling[0].extraction_error = SIBLING_NOTE
    capped = None
    if result is None and content_type in PHOTO_TYPES:
        capped = vision_limit_reached(session, admin.id)
    document.extraction = result.to_json() if result else None
    document.extraction_method = LIMIT_METHOD if capped else method
    document.extraction_error = None if result else (
        limit_message(capped[1]) if capped else
        "; ".join(dict.fromkeys(errors)) or "No reader could handle this file")[:500]
    document.attempts += 1
    document.status = InboxStatus.REVIEW
