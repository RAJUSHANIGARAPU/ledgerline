from datetime import UTC, datetime
from pathlib import Path

from fastapi import APIRouter, Form, HTTPException, Request
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse, Response
from sqlalchemy import select

from ledgerline import audit
from ledgerline.config import get_settings
from ledgerline.db import get_sessionmaker
from ledgerline.inbox import service as inbox
from ledgerline.mobile import tokens
from ledgerline.models import ApiToken
from ledgerline.web import AdminDep, ManagerDep, OwnerDep, SessionDep, UserDep, flash, render

router = APIRouter()
STATIC = Path(__file__).resolve().parent.parent / "static"
MULTIPART_SLACK = 64 * 1024


@router.get("/manifest.webmanifest", include_in_schema=False)
def manifest():
    return FileResponse(STATIC / "manifest.webmanifest", media_type="application/manifest+json")


@router.get("/sw.js", include_in_schema=False)
def service_worker():
    return FileResponse(
        STATIC / "sw.js",
        media_type="text/javascript",
        headers={"Cache-Control": "no-cache", "Service-Worker-Allowed": "/"},
    )


@router.get("/capture")
def capture_form(request: Request, user: UserDep, admin: AdminDep):
    return render(
        request,
        "mobile/capture.html",
        {
            "admin": admin,
            "max_mb": get_settings().max_upload_bytes // (1024 * 1024),
        },
    )


@router.post("/capture")
async def capture_submit(request: Request, user: OwnerDep, admin: AdminDep, session: SessionDep):
    form = await request.form()
    note = str(form.get("note") or "")
    chosen = [f for f in form.getlist("files") if hasattr(f, "read") and f.filename]
    if not chosen:
        flash(request, "Choose a photo or file first.", "error")
        return RedirectResponse("/capture", 303)
    payloads = [(f.filename, await f.read()) for f in chosen]
    results = await run_in_threadpool(
        _ingest, session, admin.id, user.id, payloads, note.strip() or None
    )
    return render(request, "mobile/captured.html", {"admin": admin, "results": results})


def _ingest(session, admin_id: int, user_id: int, payloads, subject):
    results = []
    for name, content in payloads:
        try:
            document, created = inbox.receive(
                session, admin_id, name, content, "phone", subject=subject, user_id=user_id
            )
        except inbox.InboxError as exc:
            results.append({"name": name, "error": _friendly(str(exc))})
            continue
        results.append({"name": name, "id": document.id, "duplicate": not created})
    session.commit()
    return results


def _friendly(message: str) -> str:
    if message.startswith("Only PDF"):
        return "This file type is not supported. Send a PDF, JPEG or PNG."
    return message


def _unauthorized() -> JSONResponse:
    return JSONResponse({"detail": "invalid token"}, 401, headers={"WWW-Authenticate": "Bearer"})


def _bearer(request: Request) -> str | None:
    header = request.headers.get("authorization", "")
    scheme, _, value = header.partition(" ")
    return value.strip() if scheme.lower() == "bearer" and value.strip() else None


async def _read_upload(request: Request, limit: int) -> tuple[str, bytes] | JSONResponse:
    declared = request.headers.get("content-length")
    if declared and declared.isdigit() and int(declared) > limit + MULTIPART_SLACK:
        return JSONResponse({"detail": "file too large"}, 413)
    filename = request.query_params.get("filename") or "upload"
    if request.headers.get("content-type", "").lower().startswith("multipart/form-data"):
        form = await request.form()
        upload = form.get("file")
        if not hasattr(upload, "read"):
            return JSONResponse({"detail": 'multipart field "file" is required'}, 400)
        content = await upload.read()
        filename = upload.filename or filename
    else:
        chunks, size = [], 0
        async for chunk in request.stream():
            size += len(chunk)
            if size > limit:
                return JSONResponse({"detail": "file too large"}, 413)
            chunks.append(chunk)
        content = b"".join(chunks)
    if len(content) > limit:
        return JSONResponse({"detail": "file too large"}, 413)
    return filename, content


def _store(token_value: str, filename: str, content: bytes) -> JSONResponse:
    with get_sessionmaker()() as session:
        token = tokens.authenticate(session, token_value)
        if token is None:
            return _unauthorized()
        try:
            inbox.sniff_type(content)
        except inbox.InboxError as exc:
            return JSONResponse({"detail": str(exc)}, 415)
        document, created = inbox.receive(
            session, token.administration_id, filename, content, "phone", user_id=token.user_id
        )
        audit.record(
            session,
            "api_token.upload",
            user_id=token.user_id,
            administration_id=token.administration_id,
            entity_type="inbox_document",
            entity_id=document.id,
            details={"token_id": token.id, "duplicate": not created},
        )
        token.last_used_at = datetime.now(UTC)
        session.commit()
        return JSONResponse(
            {"id": document.id, "status": document.status, "duplicate": not created},
            201 if created else 200,
        )


@router.post("/api/v1/inbox", include_in_schema=False)
async def api_upload(request: Request):
    client = request.client.host if request.client else "unknown"
    if tokens.too_many_failures(client):
        return JSONResponse({"detail": "too many attempts"}, 429, headers={"Retry-After": "600"})
    value = _bearer(request)
    if value is None:
        tokens.record_failure(client)
        return _unauthorized()
    with get_sessionmaker()() as session:
        token = tokens.authenticate(session, value)
        token_id = token.id if token else None
    if token_id is None:
        tokens.record_failure(client)
        return _unauthorized()
    if not tokens.allow_request(token_id):
        return JSONResponse({"detail": "rate limit exceeded"}, 429, headers={"Retry-After": "60"})
    limit = get_settings().max_upload_bytes
    read = await _read_upload(request, limit)
    if isinstance(read, Response):
        return read
    filename, content = read
    if not content:
        return JSONResponse({"detail": "the file is empty"}, 400)
    return await run_in_threadpool(_store, value, filename, content)


@router.get("/settings/devices")
def devices(request: Request, user: ManagerDep, admin: AdminDep, session: SessionDep):
    return _devices_page(request, session, admin)


def _devices_page(request: Request, session, admin, new_token: str | None = None):
    rows = session.scalars(
        select(ApiToken).where(ApiToken.administration_id == admin.id).order_by(ApiToken.id.desc())
    ).all()
    response = render(
        request,
        "mobile/devices.html",
        {
            "admin": admin,
            "tokens": rows,
            "new_token": new_token,
            "active_nav": "settings",
            "base_url": get_settings().public_base_url.rstrip("/"),
        },
    )
    response.headers["Cache-Control"] = "no-store"
    return response


@router.post("/settings/devices")
def create_device(
    request: Request,
    user: ManagerDep,
    admin: AdminDep,
    session: SessionDep,
    name: str = Form(""),
):
    row, token = tokens.create_token(session, user, admin.id, name)
    session.commit()
    return _devices_page(request, session, admin, new_token=token)


@router.post("/settings/devices/{token_id}/revoke")
def revoke_device(
    token_id: int,
    request: Request,
    user: ManagerDep,
    admin: AdminDep,
    session: SessionDep,
):
    row = tokens.revoke_token(session, user, admin.id, token_id)
    if row is None:
        raise HTTPException(404, "device not found")
    session.commit()
    flash(request, f"Revoked {row.name}.")
    return RedirectResponse("/settings/devices", 303)
