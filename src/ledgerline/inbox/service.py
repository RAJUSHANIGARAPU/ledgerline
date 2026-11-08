"""The single entry point for received documents (email, upload, phone)."""
import hashlib

from sqlalchemy import select
from sqlalchemy.orm import Session

from ledgerline import audit
from ledgerline.config import get_settings
from ledgerline.models import Attachment, InboxDocument, InboxStatus


class InboxError(ValueError):
    pass


ACCEPTED_TYPES = {
    "application/pdf": (b"%PDF-",),
    "image/jpeg": (b"\xff\xd8\xff",),
    "image/png": (b"\x89PNG\r\n\x1a\n",),
    "application/xml": (b"<?xml", b"<Invoice", b"<CreditNote", b"\xef\xbb\xbf<?xml"),
}


def sniff_type(content: bytes) -> str:
    """Content type from the file's own bytes; the sender's claim is not trusted."""
    head = content[:16].lstrip()
    for content_type, signatures in ACCEPTED_TYPES.items():
        if any(head.startswith(sig) for sig in signatures):
            return content_type
    raise InboxError("Only PDF, JPEG, PNG and UBL/XML files are accepted")


def receive(
    session: Session,
    administration_id: int,
    filename: str,
    content: bytes,
    source: str,
    *,
    sender: str | None = None,
    subject: str | None = None,
    email_message_id: str | None = None,
    user_id: int | None = None,
) -> tuple[InboxDocument, bool]:
    """Store a document and queue it for extraction. Returns (document, created)."""
    if not content:
        raise InboxError("The file is empty")
    if len(content) > get_settings().max_upload_bytes:
        raise InboxError("The file is too large")
    content_type = sniff_type(content)
    digest = hashlib.sha256(content).hexdigest()
    existing = session.scalar(
        select(InboxDocument).where(
            InboxDocument.administration_id == administration_id,
            InboxDocument.content_sha256 == digest,
        )
    )
    if existing:
        return existing, False
    attachment = Attachment(
        administration_id=administration_id,
        filename=(filename or "document")[:255],
        content_type=content_type,
        sha256=digest,
        size_bytes=len(content),
        content=content,
    )
    session.add(attachment)
    session.flush()
    document = InboxDocument(
        administration_id=administration_id,
        source=source,
        status=InboxStatus.NEW,
        attachment_id=attachment.id,
        content_sha256=digest,
        sender=(sender or None) and sender[:254],
        subject=(subject or None) and subject[:300],
        email_message_id=(email_message_id or None) and email_message_id[:300],
    )
    session.add(document)
    session.flush()
    audit.record(session, "inbox.receive", user_id=user_id, administration_id=administration_id,
                 entity_type="inbox_document", entity_id=document.id, details={"source": source})
    return document, True
