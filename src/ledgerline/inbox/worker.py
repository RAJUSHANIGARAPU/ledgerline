"""Background loop: extract new documents and poll the mailbox. Logs carry ids and counts only."""
import logging
import signal
import threading
import time
from collections.abc import Callable, Iterable
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session

from ledgerline.config import get_settings
from ledgerline.db import get_sessionmaker
from ledgerline.inbox import email_intake
from ledgerline.inbox.extract import pipeline
from ledgerline.models import InboxDocument, InboxStatus

log = logging.getLogger("ledgerline.worker")
MAX_ATTEMPTS = 3
BATCH_SIZE = 5
IDLE_SECONDS = 2.0
# Touched after every pass that reached the database; the container healthcheck reads its age.
HEARTBEAT = Path("/tmp/ledgerline-worker.heartbeat")  # noqa: S108


def _fail(document: InboxDocument) -> None:
    document.attempts += 1
    if document.attempts >= MAX_ATTEMPTS:
        document.status = InboxStatus.REVIEW
        document.extraction_method = "none"
        document.extraction_error = "Processing failed repeatedly; enter the invoice by hand."


def process_pending(
    session: Session, limit: int = BATCH_SIZE, only_ids: Iterable[int] | None = None
) -> int:
    """Extract up to `limit` NEW documents. Rows are locked so parallel workers never overlap."""
    stmt = (
        select(InboxDocument)
        .where(InboxDocument.status == InboxStatus.NEW, InboxDocument.attempts < MAX_ATTEMPTS)
        .order_by(InboxDocument.id)
        .limit(limit)
        .with_for_update(skip_locked=True)
    )
    if only_ids is not None:
        stmt = stmt.where(InboxDocument.id.in_(list(only_ids)))
    done = 0
    for document in session.scalars(stmt).all():
        try:
            with session.begin_nested():
                pipeline.run(session, document)
            done += 1
        except Exception:
            log.exception("processing document %s failed", document.id)
            session.refresh(document)
            _fail(document)
    session.commit()
    return done


def process_all_pending(session: Session, only_ids: Iterable[int] | None = None) -> int:
    total = 0
    ids = list(only_ids) if only_ids is not None else None
    while True:
        count = process_pending(session, BATCH_SIZE, ids)
        total += count
        if count == 0:
            return total


def run_forever(
    stop: threading.Event,
    session_factory: Callable[[], Session] | None = None,
    mailbox_factory: Callable | None = None,
    clock: Callable[[], float] = time.monotonic,
    heartbeat: Path | None = HEARTBEAT,
) -> None:
    factory = session_factory or get_sessionmaker()
    settings = get_settings()
    next_poll = clock()
    log.info("worker started (mailbox %s)", "on" if settings.imap_host else "off")
    while not stop.is_set():
        busy = False
        try:
            with factory() as session:
                busy = process_pending(session) > 0
            if heartbeat:
                heartbeat.touch()
        except Exception:
            log.exception("extraction pass failed")
        if (settings.imap_host or mailbox_factory) and clock() >= next_poll and not stop.is_set():
            try:
                with factory() as session:
                    result = email_intake.poll_once(session, mailbox_factory=mailbox_factory)
                log.info("mailbox poll: %s", result)
            except Exception:
                log.exception("mailbox poll failed")
            next_poll = clock() + settings.imap_poll_seconds
        if not busy:
            stop.wait(IDLE_SECONDS)
    log.info("worker stopped")


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    stop = threading.Event()
    for sig in (signal.SIGTERM, signal.SIGINT):
        signal.signal(sig, lambda *_: stop.set())
    run_forever(stop)
    return 0
