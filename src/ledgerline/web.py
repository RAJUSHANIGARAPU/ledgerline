"""Shared web plumbing: templates, the current user/administration, CSRF, flash messages."""
import secrets
from pathlib import Path
from typing import Annotated

from fastapi import Depends, HTTPException, Request, status
from fastapi.templating import Jinja2Templates
from sqlalchemy import select
from sqlalchemy.orm import Session

from ledgerline.config import get_settings
from ledgerline.db import get_session
from ledgerline.models import Administration, Role, User
from ledgerline.money import format_eur

TEMPLATE_DIR = Path(__file__).parent / "templates"
templates = Jinja2Templates(directory=TEMPLATE_DIR)
templates.env.filters["eur"] = format_eur
templates.env.filters["nldate"] = lambda d: d.strftime("%d-%m-%Y") if d else ""

SAFE_METHODS = {"GET", "HEAD", "OPTIONS"}


class LoginRequired(Exception):
    """Raised for anonymous requests; the app turns it into a redirect to /login."""


def csrf_token(request: Request) -> str:
    token = request.session.get("csrf")
    if not token:
        token = secrets.token_urlsafe(32)
        request.session["csrf"] = token
    return token


async def verify_csrf(request: Request) -> None:
    if request.method in SAFE_METHODS:
        return
    expected = request.session.get("csrf")
    sent = request.headers.get("x-csrf-token")
    if sent is None:
        form = await request.form()
        sent = form.get("csrf_token")
    if not expected or not isinstance(sent, str) or not secrets.compare_digest(expected, sent):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "invalid CSRF token")


SessionDep = Annotated[Session, Depends(get_session)]


def current_user(request: Request, session: SessionDep) -> User:
    user_id = request.session.get("user_id")
    user = session.get(User, user_id) if user_id else None
    if user is None or not user.is_active:
        raise LoginRequired
    return user


UserDep = Annotated[User, Depends(current_user)]


def require_owner(user: UserDep, _: None = Depends(verify_csrf)) -> User:
    """For every state-changing route: owner role and a valid CSRF token."""
    if user.role != Role.OWNER:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "read-only account")
    return user


OwnerDep = Annotated[User, Depends(require_owner)]


def current_administration(request: Request, session: SessionDep, user: UserDep) -> Administration:
    admin_id = request.session.get("administration_id")
    admin = session.get(Administration, admin_id) if admin_id else None
    if admin is None:
        admin = session.scalar(select(Administration).order_by(Administration.id))
        if admin is None:
            raise HTTPException(status.HTTP_409_CONFLICT, "no administration exists yet")
        request.session["administration_id"] = admin.id
    return admin


AdminDep = Annotated[Administration, Depends(current_administration)]


def flash(request: Request, message: str, kind: str = "success") -> None:
    # Reassign rather than append: in-place mutation does not mark the session as modified.
    queued = request.session.get("flash", [])
    request.session["flash"] = [*queued, {"kind": kind, "message": message}]


def render(request: Request, template: str, context: dict | None = None, status_code: int = 200):
    """Render with the common layout context (user, administrations, CSRF, flashes)."""
    ctx = dict(context or {})
    ctx.setdefault("csrf_token", csrf_token(request))
    ctx.setdefault("source_url", get_settings().source_url)
    ctx.setdefault("flashes", request.session.pop("flash", []))
    ctx.setdefault("active_nav", template.split("/")[0])
    return templates.TemplateResponse(request, template, ctx, status_code=status_code)
