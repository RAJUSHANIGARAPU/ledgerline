"""Mail-forwarding confirmation notices.

Gmail sends a verification link to a new forwarding address. That mail has no attachment, so
instead of dropping it, the link is shown to the administration's owners on the Inbox page.
Only the link is kept, never the mail, and it is never opened by the server.
"""
import html
import re
from datetime import UTC, datetime, timedelta
from email.message import EmailMessage
from urllib.parse import urlsplit

from sqlalchemy import select
from sqlalchemy.orm import Session

from ledgerline import audit
from ledgerline.models import ForwardingNotice

GMAIL_SENDER = "forwarding-noreply@google.com"
NOTICE_DAYS = 7
MAX_LINK = 600
LINK = re.compile(r"https://[^\s<>\"'()\[\]]+", re.IGNORECASE)


def _google_https(url: str) -> bool:
    try:
        parts = urlsplit(url)
        host = parts.hostname or ""
    except ValueError:
        return False
    return (
        parts.scheme == "https" and parts.username is None and parts.password is None
        and (host == "google.com" or host.endswith(".google.com"))
        and len(url) <= MAX_LINK
    )


def confirmation_link(message: EmailMessage) -> str | None:
    """The first https link on a google.com domain in the text of the mail, if any."""
    for part in message.walk():
        if part.get_content_type() not in ("text/plain", "text/html") or part.get_filename():
            continue
        try:
            body = part.get_content()
        except (LookupError, ValueError):
            continue
        for found in LINK.findall(html.unescape(body)):
            url = found.rstrip(".,;")
            if _google_https(url):
                return url
    return None


def record_gmail_confirmation(session: Session, admin_id: int, link: str) -> bool:
    """Create the notice unless the same link is already waiting. True when one was created."""
    now = datetime.now(UTC)
    waiting = session.scalar(select(ForwardingNotice.id).where(
        ForwardingNotice.administration_id == admin_id, ForwardingNotice.link == link,
        ForwardingNotice.dismissed_at.is_(None), ForwardingNotice.expires_at > now))
    if waiting is not None:
        return False
    notice = ForwardingNotice(administration_id=admin_id, kind="gmail", link=link,
                              expires_at=now + timedelta(days=NOTICE_DAYS))
    session.add(notice)
    session.flush()
    audit.record(session, "inbox.forwarding_notice.created", administration_id=admin_id,
                 entity_type="forwarding_notice", entity_id=notice.id,
                 details={"kind": "gmail"})
    return True


def active_notices(session: Session, admin_id: int) -> list[ForwardingNotice]:
    return list(session.scalars(
        select(ForwardingNotice).where(
            ForwardingNotice.administration_id == admin_id,
            ForwardingNotice.dismissed_at.is_(None),
            ForwardingNotice.expires_at > datetime.now(UTC))
        .order_by(ForwardingNotice.id.desc())))


def dismiss(session: Session, admin_id: int, notice_id: int, user_id: int) -> bool:
    notice = session.get(ForwardingNotice, notice_id)
    if notice is None or notice.administration_id != admin_id or notice.dismissed_at:
        return False
    notice.dismissed_at = datetime.now(UTC)
    audit.record(session, "inbox.forwarding_notice.dismissed", user_id=user_id,
                 administration_id=admin_id, entity_type="forwarding_notice",
                 entity_id=notice.id)
    return True
