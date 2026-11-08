import logging
import smtplib

from ledgerline import mail
from ledgerline.config import get_settings
from tests.account.conftest import FakeSMTP, FakeSMTPSSL, body


def test_invite_is_one_message_dutch_first_then_english():
    content = mail.invite_new_user("Bakkerij Smit", "https://books.example/invite/abc", 7)
    assert content.subject == "Uitnodiging voor Bakkerij Smit / Invitation to Bakkerij Smit"
    text = content.text
    assert text.index("Je bent uitgenodigd") < text.index("You have been invited")
    assert text.count("https://books.example/invite/abc") == 2
    assert content.html.index('lang="nl"') < content.html.index('lang="en"')
    assert 'href="https://books.example/invite/abc"' in content.html


def test_html_escapes_tenant_name_and_subject_has_no_line_breaks():
    content = mail.invite_new_user("<b>Evil</b>\nBcc: x@y.z", "https://x.example/invite/t", 7)
    assert "<b>" not in content.html and "&lt;b&gt;" in content.html
    assert "\n" not in content.subject


def test_existing_user_mail_has_no_password_link_to_set():
    content = mail.invite_existing_user("Bakkerij Smit", "https://books.example/login")
    assert "You were added to Bakkerij Smit" in content.subject
    assert "/invite/" not in content.text and "/reset/" not in content.text


def test_starttls_login_and_send(outbox):
    content = mail.password_reset("https://books.example/reset/tok", 60)
    assert mail.send("jan@example.com", content, "https://books.example/reset/tok") is True
    smtp = FakeSMTP.instances[-1]
    assert (smtp.host, smtp.port) == ("smtp.test", 587)
    assert smtp.calls == ["ehlo", "starttls", "ehlo", ("login", "mailer"), "send"]
    message = outbox[-1]
    assert message["To"] == "jan@example.com"
    assert message["From"] == "Ledgerline <noreply@test.example>"
    assert "Nieuw wachtwoord kiezen" in body(message)
    assert message.get_body(("html",)) is not None


def test_ssl_mode_uses_smtp_ssl_without_starttls(outbox, monkeypatch):
    monkeypatch.setenv("LEDGERLINE_SMTP_TLS", "ssl")
    monkeypatch.setenv("LEDGERLINE_SMTP_PORT", "465")
    get_settings.cache_clear()
    assert mail.send("a@example.com", mail.password_reset("https://x/reset/t", 60)) is True
    smtp = FakeSMTP.instances[-1]
    assert isinstance(smtp, FakeSMTPSSL) and smtp.port == 465
    assert "starttls" not in smtp.calls


def test_not_configured_logs_and_reports_failure(monkeypatch, caplog):
    monkeypatch.setenv("LEDGERLINE_SMTP_HOST", "")
    monkeypatch.setenv("LEDGERLINE_ENVIRONMENT", "production")
    get_settings.cache_clear()
    with caplog.at_level(logging.WARNING, logger="ledgerline.mail"):
        sent = mail.send("a@example.com", mail.password_reset("https://x/reset/secret", 60),
                         "https://x/reset/secret")
    assert sent is False
    assert "not configured" in caplog.text
    assert "https://x/reset/secret" not in caplog.text
    assert mail.can_deliver() is False


def test_development_prints_the_link_to_the_log(monkeypatch, caplog):
    monkeypatch.setenv("LEDGERLINE_SMTP_HOST", "")
    monkeypatch.setenv("LEDGERLINE_ENVIRONMENT", "development")
    get_settings.cache_clear()
    with caplog.at_level(logging.WARNING, logger="ledgerline.mail"):
        sent = mail.send("a@example.com", mail.password_reset("https://x/reset/tok1", 60),
                         "https://x/reset/tok1")
    assert sent is False
    assert "https://x/reset/tok1" in caplog.text
    assert mail.can_deliver() is True


def test_smtp_failure_returns_false_and_logs_no_credentials(outbox, monkeypatch, caplog):
    def boom(self, message):
        raise smtplib.SMTPRecipientsRefused({})

    monkeypatch.setattr(FakeSMTP, "send_message", boom)
    with caplog.at_level(logging.ERROR, logger="ledgerline.mail"):
        assert mail.send("a@example.com", mail.password_reset("https://x/reset/t", 60)) is False
    assert "SMTPRecipientsRefused" in caplog.text
    assert "mail-secret" not in caplog.text
