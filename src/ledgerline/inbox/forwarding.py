"""Mail-forwarding confirmation notices.

Gmail sends a verification link to a new forwarding address. That mail has no attachment, so
instead of dropping it, the link is shown to the administration's owners on the Inbox page.
Only the link is kept, never the mail, and it is never opened by the server.
"""
import html
import re
from datetime import UTC, datetime, timedelta
from email.message import EmailMessage

from sqlalchemy import select
from sqlalchemy.orm import Session

from ledgerline import audit
from ledgerline.models import ForwardingNotice

GMAIL_SENDER = "forwarding-noreply@google.com"
NOTICE_DAYS = 7
MAX_LINK = 600
MAX_ACTIVE_NOTICES = 3
# Gmail sends the verification link on its settings host; nothing else on google.com qualifies
# (sites, docs and other user-content hosts live on sibling subdomains).
GOOGLE_HOSTS = {"mail-settings.google.com", "mail.google.com"}
LINK = re.compile(r"https://[^\s<>\"'()\[\]]+", re.IGNORECASE)
LINK_SHAPE = re.compile(r"https://(?P<host>[A-Za-z0-9.-]+)(?:/[A-Za-z0-9._~:/?#@!$&*+,;=%-]*)?")


def _google_https(url: str) -> bool:
    """Only Gmail's own forwarding-verification hosts, over plain https on the default port.

    Anything a browser could read differently from urlsplit (backslash, whitespace, control
    characters, userinfo, an encoded host) is refused instead of parsed.
    """
    match = LINK_SHAPE.fullmatch(url)
    return bool(match) and len(url) <= MAX_LINK and match.group("host").lower() in GOOGLE_HOSTS


def sender_verified(message: EmailMessage) -> bool:
    """True when the receiving server recorded a passing DKIM check for google.com."""
    for header in message.get_all("Authentication-Results") or []:
        text = " ".join(str(header).lower().split())
        if re.search(r"\bdkim=pass\b[^;]*\bheader\.(?:d|i)=@?(?:[\w-]+\.)*google\.com\b", text):
            return True
    return False


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


def record_gmail_confirmation(
    session: Session, admin_id: int, link: str, verified: bool = False
) -> bool:
    """Create the notice unless the same link is already waiting or too many are. True if created.

    The From header is spoofable, so a notice is "gmail" only when DKIM passed for google.com;
    otherwise it is stored as "unverified" and shown as such.
    """
    now = datetime.now(UTC)
    waiting = session.scalar(select(ForwardingNotice.id).where(
        ForwardingNotice.administration_id == admin_id, ForwardingNotice.link == link,
        ForwardingNotice.dismissed_at.is_(None), ForwardingNotice.expires_at > now))
    if waiting is not None or len(active_notices(session, admin_id)) >= MAX_ACTIVE_NOTICES:
        return False
    notice = ForwardingNotice(administration_id=admin_id,
                              kind="gmail" if verified else "unverified", link=link,
                              expires_at=now + timedelta(days=NOTICE_DAYS))
    session.add(notice)
    session.flush()
    audit.record(session, "inbox.forwarding_notice.created", administration_id=admin_id,
                 entity_type="forwarding_notice", entity_id=notice.id,
                 details={"kind": notice.kind})
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
