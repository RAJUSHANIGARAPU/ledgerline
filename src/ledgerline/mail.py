"""Outbound email over SMTP (stdlib only). Bodies are Dutch first, then English."""
import logging
import smtplib
import ssl
from dataclasses import dataclass
from email.message import EmailMessage
from html import escape

from ledgerline.config import Settings, get_settings

log = logging.getLogger("ledgerline.mail")
TIMEOUT_SECONDS = 15


@dataclass(frozen=True)
class Content:
    subject: str
    text: str
    html: str


def is_configured(settings: Settings | None = None) -> bool:
    s = settings or get_settings()
    return bool(s.smtp_host and s.smtp_from)


def can_deliver(settings: Settings | None = None) -> bool:
    """True when a message reaches a mailbox, or a developer can read the link in the log."""
    s = settings or get_settings()
    return is_configured(s) or s.environment == "development"


def _clean(value: str) -> str:
    return " ".join(value.split())


def _render(subject_nl: str, subject_en: str, paragraphs_nl: list[str],
            paragraphs_en: list[str], link: str | None, link_nl: str, link_en: str) -> Content:
    text_parts, html_parts = [], []
    for lang, paragraphs, label in (("nl", paragraphs_nl, link_nl), ("en", paragraphs_en, link_en)):
        block = list(paragraphs)
        text_parts.append("\n\n".join(block) + (f"\n\n{label}: {link}" if link else ""))
        html = "".join(f"<p>{escape(p)}</p>" for p in block)
        if link:
            html += f'<p><a href="{escape(link)}">{escape(label)}</a></p>'
        html_parts.append(f'<div lang="{lang}">{html}</div>')
    text = "\n\n-----\n\n".join(text_parts)
    html = "<hr>".join(html_parts)
    return Content(f"{subject_nl} / {subject_en}", text,
                   f"<!doctype html><html><body>{html}</body></html>")


def invite_new_user(tenant_name: str, link: str, days: int) -> Content:
    name = _clean(tenant_name)
    return _render(
        f"Uitnodiging voor {name}", f"Invitation to {name}",
        [f"Je bent uitgenodigd om mee te werken in de boekhouding van {name}.",
         f"Kies een wachtwoord via de link. De link werkt eenmalig en {days} dagen."],
        [f"You have been invited to work in the books of {name}.",
         f"Choose a password with the link. It works once and for {days} days."],
        link, "Uitnodiging accepteren", "Accept the invitation")


def invite_existing_user(tenant_name: str, login_link: str) -> Content:
    name = _clean(tenant_name)
    return _render(
        f"Je bent toegevoegd aan {name}", f"You were added to {name}",
        [f"Je bent toegevoegd aan {name}. Log in met je bestaande account en accepteer "
         "de uitnodiging onder Account."],
        [f"You were added to {name}. Sign in with your existing account and accept the "
         "invitation under Account."],
        login_link, "Inloggen", "Sign in")


def password_reset(link: str, minutes: int) -> Content:
    return _render(
        "Wachtwoord opnieuw instellen", "Reset your password",
        [f"Er is gevraagd om je wachtwoord opnieuw in te stellen. De link werkt eenmalig en "
         f"{minutes} minuten. Heb je dit niet gevraagd, dan kun je deze mail negeren."],
        [f"A password reset was requested. The link works once and for {minutes} minutes. "
         "If you did not ask for it, ignore this message."],
        link, "Nieuw wachtwoord kiezen", "Choose a new password")


def build_message(to: str, content: Content, sender: str) -> EmailMessage:
    message = EmailMessage()
    message["From"] = sender
    message["To"] = to
    message["Subject"] = content.subject
    message.set_content(content.text)
    message.add_alternative(content.html, subtype="html")
    return message


def _connect(s: Settings) -> smtplib.SMTP:
    context = ssl.create_default_context()
    if s.smtp_tls == "ssl":
        return smtplib.SMTP_SSL(s.smtp_host, s.smtp_port, timeout=TIMEOUT_SECONDS,
                                context=context)
    client = smtplib.SMTP(s.smtp_host, s.smtp_port, timeout=TIMEOUT_SECONDS)
    if s.smtp_tls == "starttls":
        client.ehlo()
        client.starttls(context=context)
        client.ehlo()
    return client


def send(to: str, content: Content, link: str | None = None) -> bool:
    """Send a message; True only when the SMTP server accepted it. Never raises."""
    s = get_settings()
    if not is_configured(s):
        log.warning("SMTP is not configured; email to a recipient was not sent")
        if s.environment == "development" and link:
            log.warning("development mail, link for %s: %s", to, link)
        return False
    try:
        message = build_message(to, content, s.smtp_from)
        with _connect(s) as client:
            if s.smtp_user:
                client.login(s.smtp_user, s.smtp_password)
            client.send_message(message)
    except (OSError, smtplib.SMTPException, ssl.SSLError) as exc:
        log.error("sending email failed: %s", type(exc).__name__)
        return False
    return True
