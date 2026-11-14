"""Pilot measurements: upload counts per channel, time to processed, verbatim feedback."""
import csv
import io
import zipfile
from datetime import UTC, date, datetime, timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select

from ledgerline import audit, cli
from ledgerline.accounts import ratelimit
from ledgerline.config import get_settings
from ledgerline.inbox import email_intake, service
from ledgerline.main import create_app
from ledgerline.mobile import tokens
from ledgerline.models import AuditEvent, FeedbackEntry
from ledgerline.platform import pilot
from tests.inbox.factories import eml
from tests.inbox.test_email_intake import FakeMailbox
from tests.mobile.test_mobile import JPEG, bearer, pdf

TODAY = datetime.now(UTC).date()
WIDE = (TODAY - timedelta(days=1), TODAY + timedelta(days=1))


def _report(session, admin):
    [report] = [r for r in pilot.administration_reports(session, *WIDE)
                if r.administration_id == admin.id]
    return report


def test_percentile_is_nearest_rank():
    assert pilot.percentile([], 90) is None
    assert pilot.percentile([5], 90) == 5
    assert pilot.percentile(list(range(1, 11)), 90) == 9
    assert pilot.percentile([30, 10, 20], 50) == 20


def test_window_includes_the_whole_last_day():
    lo, hi = pilot.window(date(2026, 11, 1), date(2026, 11, 1))
    assert hi - lo == timedelta(days=1)


def test_feedback_csv_defuses_spreadsheet_formulas():
    row = {"id": 1, "created_at": "x", "user": "u@example.com", "administration_id": 1,
           "administration": "A", "text": "=HYPERLINK(\"http://evil\")\nsecond line"}
    parsed = list(csv.DictReader(io.StringIO(pilot.feedback_csv([row]))))
    assert parsed[0]["text"] == "'=HYPERLINK(\"http://evil\")\nsecond line"


def test_csv_does_not_prefix_empty_cells_and_keeps_other_text_verbatim():
    row = {"id": 1, "tenant_id": 1, "created_at": "x", "user": "", "administration_id": 1,
           "administration": "A", "text": "- a bullet\nplain"}
    [parsed] = csv.DictReader(io.StringIO(pilot.feedback_csv([row])))
    assert parsed["user"] == "" and parsed["text"] == "'- a bullet\nplain"
    assert parsed["tenant_id"] == "1"
    assert pilot._safe({"a": "text", "b": "x=1"}) == {"a": "text", "b": "x=1"}


def test_terminal_text_is_made_inert():
    hostile = "ok\x1b]0;owned\x07\x1b[31mred\x9b2J\r\x00 tab\there\u202eevil\nnext"
    out = pilot.printable(hostile)
    assert not any(c in out for c in "\x1b\x07\x9b\r\x00\u202e")
    assert "\t" in out and "\n" in out and "\\u001b" in out
    assert pilot.printable("é中 ok") == "é中 ok"


def test_report_text_escapes_feedback_user_and_names():
    rows = [{"created_at": "t", "user": "a\x1b[1m@x", "administration": "B\x07",
             "text": "line\x1b[2J\nsecond"}]
    out = pilot.render_text(1, [pilot.AdministrationReport(1, "N\x1bame")], rows,
                            *WIDE)
    assert "\x1b" not in out and "\x07" not in out
    assert "second" in out


def test_web_upload_success_and_failure_are_counted(client, session, admin):
    ok = [("files", ("a.pdf", pdf("web"), "application/pdf"))]
    bad = [("files", ("x.gif", b"GIF89a....", "image/gif"))]
    client.post("/inbox/upload", data={"csrf_token": client.csrf}, files=ok)
    client.post("/inbox/upload", data={"csrf_token": client.csrf}, files=bad)
    web = _report(session, admin).channels["web"]
    assert (web.ok, web.failed, web.reasons) == (1, 1, {"unsupported_type": 1})


def test_phone_capture_counts_separately_from_the_shortcut(client, session, admin, owner):
    client.post("/capture", data={"csrf_token": client.csrf},
                files=[("files", ("a.pdf", pdf("cap"), "application/pdf"))])
    client.post("/capture", data={"csrf_token": client.csrf},
                files=[("files", ("x.gif", b"GIF89a....", "image/gif"))])
    _, secret = tokens.create_token(session, owner, admin.id, "iPhone")
    session.commit()
    anon = TestClient(create_app())
    anon.post("/api/v1/inbox", headers=bearer(secret),
              files={"file": ("r.pdf", pdf("sc"), "application/pdf")})
    anon.post("/api/v1/inbox", headers=bearer(secret),
              files={"file": ("x.gif", b"GIF89a....", "image/gif")})
    anon.post("/api/v1/inbox", headers=bearer(secret), content=b"",
              params={"filename": "e.pdf"})
    channels = _report(session, admin).channels
    assert (channels["phone"].ok, channels["phone"].failed) == (1, 1)
    shortcut = channels["shortcut"]
    assert (shortcut.ok, shortcut.failed) == (1, 2)
    assert shortcut.reasons == {"unsupported_type": 1, "empty": 1}


def test_a_wrong_token_is_not_attributed_to_any_administration(session, admin, owner):
    anon = TestClient(create_app())
    anon.post("/api/v1/inbox", headers=bearer("ll_nope"),
              files={"file": ("x.gif", b"GIF89a....", "image/gif")})
    assert _report(session, admin).channels == {}


def test_mailbox_attachments_are_counted(session, admin):
    settings = get_settings().model_copy(update={
        "imap_host": "mail.example", "imap_user": "i@jouwbedrijf.example",
        "imap_default_administration_id": admin.id, "imap_allowed_senders": "",
        "imap_processed_folder": "Ledgerline/Processed"})
    raw = eml(attachments=[("ok.pdf", pdf("mail"), "application", "pdf"),
                           ("bad.pdf", b"not a pdf at all", "application", "pdf")])
    email_intake.poll_once(session, lambda _s: FakeMailbox({"1": raw}), settings)
    email = _report(session, admin).channels["email"]
    assert (email.ok, email.failed, email.reasons) == (1, 1, {"unsupported_type": 1})


def _event(session, action, admin, doc_id, at):
    session.add(AuditEvent(action=action, administration_id=admin.id,
                           entity_type="inbox_document", entity_id=doc_id, created_at=at,
                           details={"source": "upload", "channel": "web"}
                           if action == "inbox.receive" else None))


def test_time_to_processed_uses_the_first_processing_event(session, admin):
    now = datetime.now(UTC)
    for doc_id, minutes in ((101, 1), (102, 5), (103, 10)):
        _event(session, "inbox.receive", admin, doc_id, now - timedelta(minutes=minutes + 30))
        _event(session, "inbox.create_purchase", admin, doc_id, now - timedelta(minutes=30))
    _event(session, "inbox.link_duplicate", admin, 103, now)
    _event(session, "inbox.receive", admin, 104, now)
    _event(session, "inbox.receive", admin, 105, now)
    _event(session, "inbox.reject", admin, 105, now)
    session.commit()
    report = _report(session, admin)
    assert sorted(round(s / 60) for s in report.seconds_to_processed) == [1, 5, 10]
    assert round(report.median_seconds / 60) == 5
    assert round(report.p90_seconds / 60) == 10
    assert report.unprocessed == 1


def test_events_outside_the_range_are_ignored(session, admin):
    _event(session, "inbox.receive", admin, 1, datetime.now(UTC) - timedelta(days=30))
    session.commit()
    assert _report(session, admin).channels == {}


def test_real_purchase_creation_leaves_an_event_the_report_can_use(session, admin):
    document, _ = service.receive(session, admin.id, "a.pdf", pdf("real"), "upload")
    audit.record(session, "inbox.link_duplicate", administration_id=admin.id,
                 entity_type="inbox_document", entity_id=document.id)
    session.commit()
    report = _report(session, admin)
    assert len(report.seconds_to_processed) == 1 and report.unprocessed == 0


def test_feedback_is_stored_verbatim_with_user_and_administration(client, session, admin, owner):
    text = "  Fotos uploaden werkt <b>niet</b> op mijn iPhone.\n\nWaarom 2x BTW?  é中  "
    response = client.post("/feedback", data={"csrf_token": client.csrf, "message": text},
                           follow_redirects=False)
    assert response.status_code == 303
    session.expire_all()
    entry = session.scalar(select(FeedbackEntry))
    assert (entry.body, entry.user_id, entry.administration_id) == (text, owner.id, admin.id)
    assert session.scalar(select(func.count()).select_from(AuditEvent)
                          .where(AuditEvent.action == "feedback.submit")) == 1
    assert text.strip() not in str(session.scalars(select(AuditEvent.details)).all())


def test_feedback_form_rejects_blank_and_oversized_text(client, session, admin):
    for message in ("   \n", "x" * 5001):
        client.post("/feedback", data={"csrf_token": client.csrf, "message": message})
    assert session.scalar(select(func.count()).select_from(FeedbackEntry)) == 0


def test_feedback_with_nul_byte_is_a_form_error_not_a_crash(client, session, admin):
    response = client.post("/feedback", data={"csrf_token": client.csrf, "message": "a\x00b"})
    assert response.status_code == 400 and "null characters" in response.text
    assert session.scalar(select(func.count()).select_from(FeedbackEntry)) == 0


def test_oversized_feedback_is_400_and_keeps_the_text(client, admin):
    response = client.post("/feedback", data={"csrf_token": client.csrf, "message": "x" * 5001})
    assert response.status_code == 400 and "limited to 5000" in response.text


def test_feedback_is_rate_limited_per_user(client, session, admin):
    limit = ratelimit.feedback_by_user.limit
    codes = [client.post("/feedback", data={"csrf_token": client.csrf, "message": "hi"},
                         follow_redirects=False).status_code for _ in range(limit + 1)]
    assert codes[:limit] == [303] * limit and codes[-1] == 429
    assert session.scalar(select(func.count()).select_from(FeedbackEntry)) == limit


def test_bookkeeper_can_send_feedback_and_accountant_cannot(bookkeeper_client, readonly_client,
                                                            session):
    assert bookkeeper_client.get("/feedback").status_code == 200
    bookkeeper_client.post("/feedback", data={"csrf_token": bookkeeper_client.csrf,
                                              "message": "ok"})
    assert readonly_client.get("/feedback").status_code == 403
    assert readonly_client.post("/feedback", data={"csrf_token": readonly_client.csrf,
                                                   "message": "no"}).status_code == 403
    assert session.scalar(select(func.count()).select_from(FeedbackEntry)) == 1


def test_feedback_needs_a_csrf_token(client, session, admin):
    assert client.post("/feedback", data={"csrf_token": "wrong", "message": "x"}).status_code == 403


def test_administration_export_contains_the_feedback(client, session, admin):
    client.post("/feedback", data={"csrf_token": client.csrf, "message": "export me"})
    response = client.get("/settings/privacy/export.zip")
    with zipfile.ZipFile(io.BytesIO(response.content)) as zf:
        assert "export me" in zf.read("data/feedback_entries.json").decode()


def test_cli_report_and_csv_show_feedback_verbatim(client, session, admin, capsys):
    client.post("/capture", data={"csrf_token": client.csrf},
                files=[("files", ("a.jpg", JPEG, "image/jpeg"))])
    client.post("/feedback", data={"csrf_token": client.csrf, "message": "line one\nline two"})
    args = ["--from", WIDE[0].isoformat(), "--to", WIDE[1].isoformat()]
    capsys.readouterr()
    assert cli.main(["pilot-report", *args]) == 0
    out = capsys.readouterr().out
    assert "phone" in out and "line one\n" in out and "not yet processed=1" in out
    assert cli.main(["feedback-export", *args]) == 0
    [row] = list(csv.DictReader(io.StringIO(capsys.readouterr().out)))
    assert row["text"] == "line one\nline two" and row["user"] == "owner@example.com"
    assert row["tenant_id"].isdigit()


def test_cli_errors_on_an_unknown_tenant(client, admin, capsys):
    with pytest.raises(SystemExit, match="no tenant with id 999999"):
        cli.main(["feedback-export", "--tenant", "999999", "--from", "2026-01-01",
                  "--to", "2026-01-02"])


def test_cli_refuses_a_role_that_bypasses_row_level_security(owner_engine, tenant, monkeypatch,
                                                              capsys):
    from sqlalchemy.orm import sessionmaker

    from ledgerline import db

    monkeypatch.setattr(db, "get_sessionmaker", lambda: sessionmaker(owner_engine))
    args = ["--from", "2026-01-01", "--to", "2026-01-02"]
    with pytest.raises(SystemExit, match="refusing to run"):
        cli.main(["pilot-report", *args])
    assert cli.main(["pilot-report", "--tenant", str(tenant.id), "--allow-rls-bypass", *args]) == 0


def test_the_application_role_does_not_bypass_row_level_security(session):
    from ledgerline import db

    assert db.rls_bypass_reason(session) is None


def test_cli_rejects_an_inverted_range():
    with pytest.raises(SystemExit):
        cli.main(["pilot-report", "--from", "2026-02-02", "--to", "2026-01-01"])
