import time
from collections import defaultdict, deque
from typing import Annotated

from argon2 import PasswordHasher
from argon2.exceptions import VerificationError
from fastapi import APIRouter, Depends, Form, Request, status
from fastapi.responses import RedirectResponse
from sqlalchemy import func, select

from ledgerline import audit
from ledgerline.models import Administration, User
from ledgerline.web import SessionDep, UserDep, flash, render, verify_csrf

router = APIRouter()
hasher = PasswordHasher()

MAX_FAILURES = 5
FAILURE_WINDOW_SECONDS = 15 * 60
_failures: dict[str, deque[float]] = defaultdict(deque)


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


@router.post("/login", dependencies=[Depends(verify_csrf)])
def login(
    request: Request,
    session: SessionDep,
    email: Annotated[str, Form()],
    password: Annotated[str, Form()],
):
    key = email.strip().lower()
    if _locked_out(key):
        return render(request, "auth/login.html",
                      {"error": "Too many attempts. Try again in 15 minutes."}, 429)
    user = session.scalar(select(User).where(func.lower(User.email) == key))
    try:
        if user is None or not user.is_active:
            raise VerificationError
        hasher.verify(user.password_hash, password)
    except VerificationError:
        _failures[key].append(time.monotonic())
        return render(request, "auth/login.html", {"error": "Wrong email or password."}, 401)
    _failures.pop(key, None)
    if hasher.check_needs_rehash(user.password_hash):
        user.password_hash = hasher.hash(password)
    request.session.clear()
    request.session["user_id"] = user.id
    request.session["role"] = user.role
    audit.record(session, "login", user_id=user.id)
    session.commit()
    return RedirectResponse("/", status.HTTP_303_SEE_OTHER)


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
        flash(request, "Unknown administration.", "error")
    else:
        request.session["administration_id"] = administration_id
    return RedirectResponse("/", status.HTTP_303_SEE_OTHER)
