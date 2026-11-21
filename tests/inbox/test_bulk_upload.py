"""Bulk upload: one request carrying many documents.

The route takes ``list[UploadFile]`` and receives each file in turn. The only per-request limits
in the application are Starlette's multipart defaults (1000 files, 1000 fields) and the
per-file ``max_upload_bytes``; the reverse proxy adds a total body limit (deploy/hetzner).
"""

from sqlalchemy import func, select

from ledgerline.models import InboxDocument
from tests.inbox.factories import ubl
from tests.inbox.test_review_flow import upload

COUNT = 50


def bill(n: int) -> tuple[str, bytes, str]:
    content = ubl([(f"Service {n}", 1000 + n, "S", 21)], number=f"BULK-{n:04d}")
    return f"bill-{n:04d}.xml", content, "application/xml"


def stored(session) -> int:
    session.expire_all()
    return session.scalar(select(func.count()).select_from(InboxDocument))


def test_fifty_distinct_documents_in_one_request_are_all_received(client, session, admin):
    response = upload(client, session, *(bill(n) for n in range(COUNT)))

    assert response.headers["location"] == "/inbox?tab=new"
    assert stored(session) == COUNT



def test_resending_documents_reports_them_as_duplicates_and_keeps_the_new_one(
    client, session, admin
):
    upload(client, session, *(bill(n) for n in range(COUNT)))
    first_ids = set(session.scalars(select(InboxDocument.id)))

    response = upload(client, session, *(bill(n) for n in range(5)), bill(COUNT))

    assert stored(session) == COUNT + 1
    location = response.headers["location"]
    assert location.startswith("/inbox?dup=")
    reported = {int(i) for i in location.removeprefix("/inbox?dup=").split(",")}
    assert len(reported) == 5 and reported <= first_ids


def test_one_unacceptable_file_does_not_stop_the_rest(client, session, admin):
    files = [bill(n) for n in range(10)]
    files.insert(4, ("notes.txt", b"just some text", "text/plain"))

    upload(client, session, *files)

    assert stored(session) == 10
