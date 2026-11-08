import time
from collections import defaultdict, deque
from typing import Annotated

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError
from fastapi import APIRouter, Depends, Form, HTTPException, Request, status
from fastapi.responses import RedirectResponse
from sqlalchemy import func, select

from ledgerline import audit
from ledgerline.accounts import events, ratelimit, totp
from ledgerline.db import set_tenant
from ledgerline.models import Administration, Membership, User
from ledgerline.web import LoginDep, SessionDep, UserDep, render, verify_csrf

router = APIRouter()
hasher = PasswordHasher()

MAX_FAILURES = 5
FAILURE_WINDOW_SECONDS = 15 * 60
_failures: dict[str, deque[float]] = defaultdict(deque)
DUMMY_HASH = hasher.hash("not-a-real-password-for-timing")


def hash_password(password: str) -> str:
    if len(password) < 12:
        raise ValueError("password must be at least 12 characters")
    return hasher.hash(password)


def _locked_out(key: str) -> bool:
    window = _failures[key]
    cutoff = time.monotonic() - FAILURE_WINDOW_SECONDS
    while window and window[0] < cutoff:
        window.popleft()
    return len(window) >= MAX_FAILURES


@router.get("/login")
def login_form(request: Request):
    return render(request, "auth/login.html")


def _password_ok(user: User | None, password: str) -> bool:
    """Verify in constant shape: an unknown or invitation-only user costs one hash too."""
    candidate = user.password_hash if user is not None and user.is_active else DUMMY_HASH
    if not candidate.startswith("$argon2"):
        candidate = DUMMY_HASH
    try:
        hasher.verify(candidate, password)
    except (VerificationError, InvalidHashError):
        return False
    return user is not None and user.is_active and candidate == user.password_hash


@router.post("/login", dependencies=[Depends(verify_csrf)])
def login(
    request: Request,
    session: SessionDep,
    email: Annotated[str, Form()],
    password: Annotated[str, Form()],
):
    key = email.strip().lower()
    ip = request.client.host if request.client else "unknown"
    if _locked_out(key) or ratelimit.password_failures_by_ip.blocked(ip):
        return render(request, "auth/login.html",
                      {"error": "Too many attempts. Try again in 15 minutes."}, 429)
    user = session.scalar(select(User).where(func.lower(User.email) == key))
    if not _password_ok(user, password):
        _failures[key].append(time.monotonic())
        ratelimit.password_failures_by_ip.hit(ip)
        if user is not None:
            events.record_for_user(session, user.id, "login.failure", {"step": "password"})
            session.commit()
        return render(request, "auth/login.html", {"error": "Wrong email or password."}, 401)
    _failures.pop(key, None)
    if hasher.check_needs_rehash(user.password_hash):
        user.password_hash = hasher.hash(password)
    request.session.clear()
    if totp.enabled(session, user.id) is not None:
        request.session["pw_ok"] = {"u": user.id, "t": int(time.time()), "n": 0}
        session.commit()
        return RedirectResponse("/login/totp", status.HTTP_303_SEE_OTHER)
    return complete_login(request, session, user)


def complete_login(request: Request, session, user: User):
    """The credentials are fully verified (password, and code when enrolled): start the session."""
    request.session.clear()
    request.session["user_id"] = user.id
    request.session["sv"] = user.session_version
    session.commit()
    return _after_login(request, session, user)


def _memberships(session, user: User) -> list[Membership]:
    return list(session.scalars(
        select(Membership).where(Membership.user_id == user.id, Membership.status == "active")
        .order_by(Membership.id)
    ).unique())


def _enter_tenant(request: Request, session, user: User, membership: Membership, action: str):
    request.session["tenant_id"] = membership.tenant_id
    request.session.pop("administration_id", None)
    set_tenant(session, membership.tenant_id)
    audit.record(session, action, user_id=user.id)
    session.commit()
    return RedirectResponse("/", status.HTTP_303_SEE_OTHER)


def _after_login(request: Request, session, user: User):
    memberships = _memberships(session, user)
    if not memberships:
        if session.scalar(select(Membership.id).where(
                Membership.user_id == user.id, Membership.status == "pending")):
            return RedirectResponse("/account", status.HTTP_303_SEE_OTHER)
        request.session.clear()
        return render(request, "auth/login.html", {
            "error": "Your account is not a member of any workspace yet. "
                     "Ask the owner of the books to add you."}, 403)
    if len(memberships) == 1:
        return _enter_tenant(request, session, user, memberships[0], "login")
    return RedirectResponse("/tenants", status.HTTP_303_SEE_OTHER)


@router.get("/tenants")
def tenant_list(request: Request, session: SessionDep, user: LoginDep):
    memberships = _memberships(session, user)
    if len(memberships) == 1 and request.session.get("tenant_id") is None:
        return _enter_tenant(request, session, user, memberships[0], "login")
    if not memberships:
        request.session.clear()
        return RedirectResponse("/login", status.HTTP_303_SEE_OTHER)
    return render(request, "auth/tenants.html", {"memberships": memberships})


@router.post("/tenants", dependencies=[Depends(verify_csrf)])
def tenant_select(
    request: Request, session: SessionDep, user: LoginDep, tenant_id: Annotated[int, Form()],
):
    chosen = next((m for m in _memberships(session, user) if m.tenant_id == tenant_id), None)
    if chosen is None:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "not a member of this tenant")
    return _enter_tenant(request, session, user, chosen, "tenant.select")


@router.post("/logout", dependencies=[Depends(verify_csrf)])
def logout(request: Request):
    request.session.clear()
    return RedirectResponse("/login", status.HTTP_303_SEE_OTHER)


@router.post("/administration/switch", dependencies=[Depends(verify_csrf)])
def switch_administration(
    request: Request, session: SessionDep, user: UserDep,
    administration_id: Annotated[int, Form()],
):
    if session.get(Administration, administration_id) is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "administration not found")
    request.session["administration_id"] = administration_id
    return RedirectResponse("/", status.HTTP_303_SEE_OTHER)
