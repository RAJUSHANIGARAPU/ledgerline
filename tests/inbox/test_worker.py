import threading

from sqlalchemy import select

from ledgerline.inbox import service, worker
from ledgerline.inbox.extract import pipeline
from ledgerline.models import InboxDocument, InboxStatus
from tests.inbox.factories import invoice_html, pdf_from_html, pdf_without_text, ubl


def add(session, admin, content, name):
    doc, _ = service.receive(session, admin.id, name, content, "upload")
    session.commit()
    return doc


def test_process_pending_reads_new_documents_in_batches(session, admin):
    for i in range(4):
        add(session, admin, ubl([("Hosting", 1000 + i, "S", 21)], number=f"N-{i}"), f"{i}.xml")
    assert worker.process_pending(session, limit=3) == 3
    assert worker.process_pending(session, limit=3) == 1
    assert worker.process_pending(session, limit=3) == 0
    docs = session.scalars(select(InboxDocument)).all()
    assert {d.status for d in docs} == {InboxStatus.REVIEW} and all(d.attempts == 1 for d in docs)


def test_only_ids_limits_the_batch(session, admin):
    a = add(session, admin, pdf_from_html(invoice_html()), "a.pdf")
    b = add(session, admin, pdf_without_text(), "b.pdf")
    assert worker.process_all_pending(session, [b.id]) == 1
    session.refresh(a)
    assert a.status == InboxStatus.NEW


def test_unexpected_failures_stop_after_three_attempts(session, admin, monkeypatch):
    doc = add(session, admin, pdf_from_html(invoice_html()), "a.pdf")

    def boom(*_a):
        raise RuntimeError("db hiccup")

    monkeypatch.setattr(pipeline, "run", boom)
    for expected in (1, 2):
        worker.process_pending(session)
        session.refresh(doc)
        assert doc.attempts == expected and doc.status == InboxStatus.NEW
    worker.process_pending(session)
    session.refresh(doc)
    assert doc.attempts == 3 and doc.status == InboxStatus.REVIEW
    assert "failed repeatedly" in doc.extraction_error
    assert worker.process_pending(session) == 0


def test_loop_processes_then_stops_on_signal(session, admin, database_url, tmp_path):
    from ledgerline.db import get_sessionmaker

    doc = add(session, admin, pdf_from_html(invoice_html()), "a.pdf")
    stop = threading.Event()

    class Mailbox:
        polled = 0

        def unseen(self):
            Mailbox.polled += 1
            stop.set()
            return []

        def close(self):
            pass

    heartbeat = tmp_path / "heartbeat"
    worker.run_forever(stop, get_sessionmaker(), mailbox_factory=lambda _s: Mailbox(),
                       heartbeat=heartbeat)
    session.refresh(doc)
    assert doc.status == InboxStatus.REVIEW and Mailbox.polled == 1
    assert heartbeat.exists()
