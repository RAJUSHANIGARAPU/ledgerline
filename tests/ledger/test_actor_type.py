"""Writes record who they came from: a user, the worker, a CLI command, or unknown."""
from datetime import date

from sqlalchemy import select

from ledgerline import audit, cli
from ledgerline.actor import WORKER, acting_as
from ledgerline.inbox import worker
from ledgerline.ledger.service import LineSpec, post_entry, reverse_entry
from ledgerline.models import AuditEvent, JournalEntry
from tests.helpers import account
from tests.vat.booking import approve_period, book_sale

D = date(2026, 2, 10)


def _post(session, admin, user_id=None):
    specs = [LineSpec(account(session, admin, "1000").id, debit_cents=100),
             LineSpec(account(session, admin, "8000").id, credit_cents=100)]
    return post_entry(session, admin.id, D, "probe", specs, user_id=user_id)


def _actor(session, model, **where):
    session.expire_all()
    return session.scalar(select(model.actor_type).where(
        *(getattr(model, k) == v for k, v in where.items())))


def test_web_posting_is_a_user(client, session, admin):
    book_sale(session, admin, "V21", 10000, D)
    session.commit()
    approve_period(client)
    client.post("/vat/settle", data={"kind": "quarter", "year": "2026", "number": "1",
                                     "csrf_token": client.csrf})
    assert _actor(session, JournalEntry, source_type="vat_settlement:2026-Q1") == "user"
    assert _actor(session, AuditEvent, action="vat.settle") == "user"


def test_worker_posting_is_worker(session, admin, monkeypatch):
    def run(_stop):
        entry = _post(session, admin)
        audit.record(session, "probe.worker", administration_id=admin.id)
        session.commit()
        assert entry.actor_type == WORKER

    monkeypatch.setattr(worker, "run_forever", run)
    monkeypatch.setattr(worker.signal, "signal", lambda *_: None)
    assert worker.main() == 0
    assert _actor(session, JournalEntry, description="probe") == "worker"
    assert _actor(session, AuditEvent, action="probe.worker") == "worker"


def test_cli_command_is_system(session, admin, monkeypatch):
    def command(_args):
        _post(session, admin)
        audit.record(session, "probe.cli", administration_id=admin.id)
        session.commit()
        return 0

    monkeypatch.setattr(cli, "create_administration", command)
    assert cli.main(["create-administration", "--name", "X", "--legal-form", "bv"]) == 0
    assert _actor(session, JournalEntry, description="probe") == "system"
    assert _actor(session, AuditEvent, action="probe.cli") == "system"


def test_no_context_and_no_user_is_unknown_and_the_context_does_not_leak(session, admin):
    with acting_as(WORKER):
        pass
    entry = _post(session, admin)
    audit.record(session, "probe.none", administration_id=admin.id)
    session.commit()
    assert entry.actor_type == "unknown"
    assert _actor(session, AuditEvent, action="probe.none") == "unknown"


def test_a_user_id_wins_over_the_background_context(session, admin, owner):
    with acting_as(WORKER):
        entry = _post(session, admin, user_id=owner.id)
    assert entry.actor_type == "user"


def test_reversal_carries_the_actor(session, admin, owner):
    entry = _post(session, admin, user_id=owner.id)
    with acting_as(WORKER):
        reversal = reverse_entry(session, entry, D, "probe")
    assert reversal.actor_type == "worker"
