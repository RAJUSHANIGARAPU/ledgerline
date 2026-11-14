"""Poll a mailbox over IMAP (SSL) and feed attachments into the inbox.

Mail is moved to another folder, never deleted outright.
"""
import contextlib
import imaplib
import logging
import re
from dataclasses import dataclass
from email import policy
from email.message import EmailMessage
from email.parser import BytesParser
from email.utils import getaddresses, parseaddr
from typing import Protocol

from sqlalchemy import func, select, text
from sqlalchemy.orm import Session

from ledgerline.config import Settings, get_settings
from ledgerline.db import set_tenant
from ledgerline.inbox import forwarding, service
from ledgerline.models import Administration, Tenant

log = logging.getLogger(__name__)
WANTED_SUFFIXES = (".pdf", ".xml", ".jpg", ".jpeg", ".png")
WANTED_TYPES = {"application/pdf", "application/xml", "text/xml", "image/jpeg", "image/png"}
PLUS_ADDRESS = re.compile(r"^([^+@]+)\+([a-z0-9]{20,64})@", re.IGNORECASE)


class Mailbox(Protocol):
    def unseen(self) -> list[str]: ...
    def fetch(self, uid: str) -> bytes: ...
    def move(self, uid: str, folder: str) -> None: ...
    def close(self) -> None: ...


class ImapMailbox:
    """The few IMAP operations intake needs, over a connected imaplib client."""

    def __init__(self, client: imaplib.IMAP4, folder: str):
        self.client = client
        status, _ = client.select(f'"{folder}"')
        if status != "OK":
            raise RuntimeError("could not open the mailbox folder")
        self.can_move = b"MOVE" in b" ".join(
            part if isinstance(part, bytes) else str(part).encode()
            for part in (client.capabilities if hasattr(client, "capabilities") else ())
        ).upper()
        self.known_folders: set[str] = set()

    def unseen(self) -> list[str]:
        status, data = self.client.uid("SEARCH", None, "UNSEEN")
        if status != "OK" or not data or not data[0]:
            return []
        return [u.decode() for u in data[0].split()]

    def fetch(self, uid: str) -> bytes:
        status, data = self.client.uid("FETCH", uid, "(BODY.PEEK[])")
        if status != "OK" or not data or not isinstance(data[0], tuple):
            raise RuntimeError("could not fetch a message")
        return data[0][1]

    def _ensure(self, folder: str) -> None:
        if folder not in self.known_folders:
            self.client.create(f'"{folder}"')
            self.known_folders.add(folder)

    def move(self, uid: str, folder: str) -> None:
        self._ensure(folder)
        if self.can_move:
            status, _ = self.client.uid("MOVE", uid, f'"{folder}"')
            if status != "OK":
                raise RuntimeError("could not move a message")
            return
        status, _ = self.client.uid("COPY", uid, f'"{folder}"')
        if status != "OK":
            raise RuntimeError("could not copy a message")
        self.client.uid("STORE", uid, "+FLAGS", r"(\Deleted)")
        self.client.expunge()

    def close(self) -> None:
        with contextlib.suppress(imaplib.IMAP4.error, OSError):
            self.client.logout()


def connect(settings: Settings) -> Mailbox:
    client = imaplib.IMAP4_SSL(settings.imap_host, settings.imap_port, timeout=30)
    client.login(settings.imap_user, settings.imap_password)
    return ImapMailbox(client, settings.imap_folder)


@dataclass
class PollResult:
    messages: int = 0
    documents: int = 0
    duplicates: int = 0
    rejected: int = 0
    skipped: int = 0

    def __str__(self) -> str:
        return (f"{self.messages} messages, {self.documents} new documents, "
                f"{self.duplicates} duplicates, {self.rejected} rejected, {self.skipped} left")


def rejected_folder(settings: Settings) -> str:
    processed = settings.imap_processed_folder
    if "/" in processed:
        return processed.rsplit("/", 1)[0] + "/Rejected"
    return processed + "-Rejected"


def sender_allowed(sender: str, allowed: str) -> bool:
    rules = [r.strip().lower() for r in allowed.split(",") if r.strip()]
    if not rules:
        return True
    sender = sender.lower()
    return any(
        sender.endswith(rule) if rule.startswith("@") else sender == rule for rule in rules
    )


def inbox_address(settings: Settings, token: str) -> str | None:
    """The address that routes mail to the administration with this token."""
    local, _, domain = settings.imap_user.partition("@")
    return f"{local}+{token}@{domain}" if local and domain else None


def route_token(session: Session, token: str) -> tuple[int, int] | None:
    """(administration id, tenant id) for an exact inbox token, through the definer function."""
    row = session.execute(
        text("SELECT administration_id, tenant_id FROM ledgerline_route_inbox_token(:t)"),
        {"t": token},
    ).first()
    return (row[0], row[1]) if row else None


def _default_administration(session: Session, settings: Settings) -> int | None:
    """Single-tenant installs keep the configured default; with several tenants, none."""
    tenant_ids = list(session.scalars(select(Tenant.id)))
    if len(tenant_ids) != 1:
        return None
    set_tenant(session, tenant_ids[0])
    default = settings.imap_default_administration_id
    return default if session.get(Administration, default) is not None else None


def route_by_token(session: Session, message: EmailMessage, settings: Settings) -> int | None:
    """The administration for an exact inbox token only; no single-tenant default."""
    base = settings.imap_user.split("@")[0].lower() if "@" in settings.imap_user else None
    headers = [message.get_all(h, []) for h in ("To", "Delivered-To", "X-Original-To", "Cc")]
    for _, address in getaddresses([str(v) for group in headers for v in group]):
        match = PLUS_ADDRESS.match(address)
        if match and (base is None or match.group(1).lower() == base):
            routed = route_token(session, match.group(2).lower())
            if routed is not None:
                set_tenant(session, routed[1])
                return routed[0]
    return None


def route_administration(
    session: Session, message: EmailMessage, settings: Settings
) -> int | None:
    """The administration a mail belongs to; the session is moved into that tenant."""
    routed = route_by_token(session, message, settings)
    return routed if routed is not None else _default_administration(session, settings)


def _forwarding_confirmation(
    session: Session, message: EmailMessage, sender: str, settings: Settings
) -> bool:
    """Handle a Gmail forwarding-confirmation mail sent to a valid inbox address."""
    if sender != forwarding.GMAIL_SENDER:
        return False
    admin_id = route_by_token(session, message, settings)
    if admin_id is None:
        return False
    link = forwarding.confirmation_link(message)
    if link is None:
        log.warning("forwarding confirmation without a usable link; nothing shown")
    elif forwarding.record_gmail_confirmation(session, admin_id, link):
        log.info("forwarding confirmation shown to the owners")
    return True


def _attachments(message: EmailMessage):
    for part in message.walk():
        if part.is_multipart() or part.get_content_type() == "message/rfc822":
            continue
        filename = part.get_filename()
        disposition = part.get_content_disposition()
        if not filename and disposition != "attachment":
            continue
        if disposition == "inline" and part.get("Content-ID") and (
            part.get_content_maintype() == "image"
        ):
            continue
        wanted = part.get_content_type() in WANTED_TYPES or (filename or "").lower().endswith(
            WANTED_SUFFIXES)
        if not wanted:
            continue
        payload = part.get_payload(decode=True)
        if payload:
            yield filename or "attachment", payload


def _clean_header(value: object) -> str | None:
    text = " ".join(str(value or "").split())
    return text or None


def process_message(
    session: Session, message: EmailMessage, settings: Settings, result: PollResult
) -> str:
    """Store the attachments. Returns 'processed', 'rejected' or 'skipped' (leave in place)."""
    sender = parseaddr(str(message.get("From", "")))[1].lower()
    if _forwarding_confirmation(session, message, sender, settings):
        return "processed"
    if not sender_allowed(sender, settings.imap_allowed_senders):
        log.info("mail from a sender outside the allow-list was set aside")
        return "rejected"
    admin_id = route_administration(session, message, settings)
    if admin_id is None:
        if session.scalar(select(func.count()).select_from(Tenant)) > 1:
            log.info("mail without a valid inbox address was set aside")
            return "rejected"
        log.warning("no administration to route mail to; leaving it in the mailbox")
        return "skipped"
    subject = _clean_header(message.get("Subject"))
    message_id = _clean_header(message.get("Message-ID"))
    found = 0
    for filename, payload in _attachments(message):
        found += 1
        try:
            with session.begin_nested():
                _, created = service.receive(
                    session, admin_id, filename, payload, "email", sender=sender,
                    subject=subject, email_message_id=message_id,
                )
        except service.InboxError as exc:
            log.info("attachment not accepted: %s", exc)
            service.record_rejection(session, admin_id, "email", exc)
            continue
        if created:
            result.documents += 1
        else:
            result.duplicates += 1
    if not found:
        log.warning("mail without a usable attachment; nothing created")
    return "processed"


def poll_once(
    session: Session, mailbox_factory=None, settings: Settings | None = None
) -> PollResult:
    settings = settings or get_settings()
    result = PollResult()
    mailbox = (mailbox_factory or connect)(settings)
    try:
        for uid in mailbox.unseen():
            result.messages += 1
            try:
                message = BytesParser(policy=policy.default).parsebytes(mailbox.fetch(uid))
                outcome = process_message(session, message, settings, result)
                session.commit()
                if outcome == "processed":
                    mailbox.move(uid, settings.imap_processed_folder)
                elif outcome == "rejected":
                    result.rejected += 1
                    mailbox.move(uid, rejected_folder(settings))
                else:
                    result.skipped += 1
            except Exception:
                session.rollback()
                result.skipped += 1
                log.exception("could not process a message; it stays in the mailbox")
    finally:
        mailbox.close()
    return result
