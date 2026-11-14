"""Invitations, password reset, two-factor login and the account pages."""
import time
from typing import Annotated

from argon2.exceptions import InvalidHashError, VerificationError
from fastapi import APIRouter, BackgroundTasks, Depends, Form, HTTPException, Request
from fastapi.responses import RedirectResponse
from sqlalchemy import delete, func, select

from ledgerline import audit, mail
from ledgerline.accounts import events, invitations, passwords, ratelimit, tokens, totp
from ledgerline.auth import complete_login, hasher
from ledgerline.config import get_settings
from ledgerline.db import set_tenant
from ledgerline.models import Membership, RecoveryCode, Role, Tenant, User, UserTotp
from ledgerline.platform import validation as v
from ledgerline.web import (
    LoginDep,
    ManagerDep,
    SessionDep,
    flash,
    render,
    verify_csrf,
)

router = APIRouter()

PASSWORD_STEP_SECONDS = 300
TOTP_ATTEMPTS_PER_LOGIN = 5
INVITE_NOTICE = "If {address} can be invited, the invitation email was sent."
INVITE_FAILED = ("The invitation email to {address} could not be sent. Check the email "
                 "settings, then press Resend invitation in the Users list.")
RESET_NOTICE = "If an account exists for that address, we sent a link to reset the password."


def _base_url() -> str:
    return get_settings().public_base_url.rstrip("/")


def _ip(request: Request) -> str:
    return request.client.host if request.client else "unknown"


def _user_by_email(session, email: str) -> User | None:
    return session.scalar(select(User).where(func.lower(User.email) == email.strip().lower()))


def _verify_password(user: User, password: str) -> bool:
    try:
        hasher.verify(user.password_hash, password)
        return True
    except (VerificationError, InvalidHashError):
        return False


def _no_store(response):
    response.headers["Cache-Control"] = "no-store"
    return response


def _invalid_link(request: Request):
    return render(request, "auth/link_invalid.html", status_code=404)


# --- invitations -------------------------------------------------------------------------

def _deliver(to: str, content: mail.Content, link: str) -> None:
    mail.send(to, content, link)


def _finish_invitation(request: Request, session, user: User, invitation, role: str):
    """Send now, audit the real outcome and tell the owner what happened."""
    ok = invitations.deliver(session, invitation, role=role, actor_id=user.id, source="app")
    session.commit()
    if ok:
        flash(request, INVITE_NOTICE.format(address=invitation.address))
    else:
        flash(request, INVITE_FAILED.format(address=invitation.address), "error")
    return RedirectResponse("/settings", 303)


@router.post("/settings/users")
def invite_user(
    request: Request, session: SessionDep, user: ManagerDep,
    email: Annotated[str, Form()] = "", role: Annotated[str, Form()] = "accountant",
):
    address, err = v.check_email(email)
    err = err or (None if address else "Email is required.")
    if role not in set(Role):
        err = "Unknown role."
    if not err and not mail.can_deliver():
        err = "Email is not set up on this server, so invitations cannot be sent."
    if not err and not ratelimit.invite_by_user.hit(str(user.id)):
        err = "Too many invitations. Try again later."
    if err:
        flash(request, err, "error")
        return RedirectResponse("/settings", 303)

    tenant: Tenant = request.state.tenant
    invitation = invitations.prepare(session, tenant, address, role)
    if invitation is None:
        session.commit()
        flash(request, INVITE_NOTICE.format(address=address.lower()))
        return RedirectResponse("/settings", 303)
    return _finish_invitation(request, session, user, invitation, role)


@router.post("/settings/users/{user_id}/resend")
def resend_invitation(request: Request, user_id: int, session: SessionDep, user: ManagerDep):
    tenant: Tenant = request.state.tenant
    if not mail.can_deliver():
        flash(request, "Email is not set up on this server, so invitations cannot be sent.",
              "error")
        return RedirectResponse("/settings", 303)
    if not ratelimit.invite_by_user.hit(str(user.id)):
        flash(request, "Too many invitations. Try again later.", "error")
        return RedirectResponse("/settings", 303)
    invitation = invitations.prepare_resend(session, tenant, user_id)
    if invitation is None:
        raise HTTPException(404, "no pending invitation")
    role = session.scalar(select(Membership.role).where(Membership.id == invitation.membership_id))
    return _finish_invitation(request, session, user, invitation, role)


@router.get("/invite/{token}")
def invite_form(request: Request, session: SessionDep, token: str):
    row = tokens.lookup(session, token, "invite")
    if row is None or row.membership_id is None:
        return _invalid_link(request)
    membership = session.get(Membership, row.membership_id)
    target = session.get(User, row.user_id)
    return _no_store(render(request, "auth/invite.html", {
        "tenant_name": membership.tenant.name, "needs_password": not target.is_active,
        "email": target.email, "token": token}))


@router.post("/invite/{token}", dependencies=[Depends(verify_csrf)])
def invite_accept(
    request: Request, session: SessionDep, token: str,
    password: Annotated[str, Form()] = "", confirm: Annotated[str, Form()] = "",
):
    row = tokens.lookup(session, token, "invite")
    if row is None or row.membership_id is None:
        return _invalid_link(request)
    membership = session.get(Membership, row.membership_id)
    target = session.get(User, row.user_id)
    needs_password = not target.is_active
    if needs_password:
        err = passwords.check_new_password(password, confirm, target.email)
        if err:
            return render(request, "auth/invite.html", {
                "tenant_name": membership.tenant.name, "needs_password": True,
                "email": target.email, "token": token, "error": err}, 422)
    if not tokens.consume(session, row):
        return _invalid_link(request)
    if needs_password:
        target.password_hash = hasher.hash(password)
        target.is_active = True
        target.session_version += 1
    membership.status = "active"
    events.record_for_user(session, target.id, "invite.accepted", {"role": membership.role},
                           only_tenant=membership.tenant_id)
    session.commit()
    flash(request, "Invitation accepted. Sign in to continue.")
    return RedirectResponse("/login", 303)


@router.post("/account/invitations/{membership_id}/{decision}")
def answer_invitation(request: Request, session: SessionDep, user: LoginDep,
                      membership_id: int, decision: str, _: None = Depends(verify_csrf)):
    membership = session.scalar(select(Membership).where(
        Membership.id == membership_id, Membership.user_id == user.id,
        Membership.status == "pending"))
    if membership is None or decision not in ("accept", "decline"):
        raise HTTPException(404, "invitation not found")
    tenant_id = membership.tenant_id
    if decision == "accept":
        membership.status = "active"
        session.flush()
        events.record_for_user(session, user.id, "invite.accepted",
                               {"role": membership.role}, only_tenant=tenant_id)
        flash(request, "Invitation accepted.")
    else:
        session.delete(membership)
        flash(request, "Invitation declined.")
    session.commit()
    return RedirectResponse("/account", 303)


# --- password reset -----------------------------------------------------------------------

@router.get("/forgot")
def forgot_form(request: Request):
    return render(request, "auth/forgot.html")


@router.post("/forgot", dependencies=[Depends(verify_csrf)])
def forgot(request: Request, background: BackgroundTasks, session: SessionDep,
           email: Annotated[str, Form()] = ""):
    key = email.strip().lower()
    allowed_ip = ratelimit.reset_by_ip.hit(_ip(request))
    allowed_email = ratelimit.reset_by_email.hit(key)
    user = _user_by_email(session, key) if key and allowed_ip and allowed_email else None
    if user is not None and user.is_active:
        token = tokens.issue(session, user.id, "reset")
        events.record_for_user(session, user.id, "reset.requested")
        session.commit()
        link = f"{_base_url()}/reset/{token}"
        background.add_task(_deliver, user.email,
                            mail.password_reset(link, int(tokens.RESET_TTL.total_seconds() // 60)),
                            link)
    flash(request, RESET_NOTICE)
    return RedirectResponse("/login", 303)


def _live_reset(session, token: str):
    row = tokens.lookup(session, token, "reset")
    user = session.get(User, row.user_id) if row else None
    return (row, user) if user is not None and user.is_active else (None, None)


@router.get("/reset/{token}")
def reset_form(request: Request, session: SessionDep, token: str):
    row, _ = _live_reset(session, token)
    if row is None:
        return _invalid_link(request)
    return _no_store(render(request, "auth/reset.html", {"token": token}))


@router.post("/reset/{token}", dependencies=[Depends(verify_csrf)])
def reset_password(request: Request, session: SessionDep, token: str,
                   password: Annotated[str, Form()] = "", confirm: Annotated[str, Form()] = ""):
    row, user = _live_reset(session, token)
    if row is None:
        return _invalid_link(request)
    err = passwords.check_new_password(password, confirm, user.email)
    if err:
        return render(request, "auth/reset.html", {"token": token, "error": err}, 422)
    if not tokens.consume(session, row):
        return _invalid_link(request)
    tokens.invalidate_resets(session, user.id)
    user.password_hash = hasher.hash(password)
    user.session_version += 1
    events.record_for_user(session, user.id, "reset.completed")
    session.commit()
    flash(request, "Password changed. Sign in with the new password.")
    return RedirectResponse("/login", 303)


# --- two-factor login step ----------------------------------------------------------------

def _marker(request: Request) -> dict | None:
    marker = request.session.get("pw_ok")
    if not isinstance(marker, dict) or time.time() - marker.get("t", 0) > PASSWORD_STEP_SECONDS:
        request.session.pop("pw_ok", None)
        return None
    return marker


@router.get("/login/totp")
def totp_form(request: Request):
    if _marker(request) is None:
        return RedirectResponse("/login", 303)
    return render(request, "auth/totp.html")


@router.post("/login/totp", dependencies=[Depends(verify_csrf)])
def totp_login(request: Request, session: SessionDep, code: Annotated[str, Form()] = ""):
    marker = _marker(request)
    user = session.get(User, marker["u"]) if marker else None
    if marker is None or user is None or not user.is_active:
        return RedirectResponse("/login", 303)
    row = totp.enabled(session, user.id)
    if row is None or ratelimit.totp_failures.blocked(str(user.id)):
        request.session.clear()
        return render(request, "auth/login.html",
                      {"error": "Too many attempts. Try again in 15 minutes."}, 429)
    recovery = not totp.is_totp_code(code)
    ok = (totp.use_recovery_code(session, user.id, code) if recovery
          else totp.verify_code(session, row, code))
    if ok:
        if recovery:
            events.record_for_user(session, user.id, "2fa.recovery_used")
        return complete_login(request, session, user)
    ratelimit.totp_failures.hit(str(user.id))
    events.record_for_user(session, user.id, "login.failure", {"step": "second_factor"})
    session.commit()
    attempts = marker["n"] + 1
    if attempts >= TOTP_ATTEMPTS_PER_LOGIN:
        request.session.clear()
        flash(request, "Too many wrong codes. Sign in again.", "error")
        return RedirectResponse("/login", 303)
    request.session["pw_ok"] = {**marker, "n": attempts}
    return render(request, "auth/totp.html", {"error": "That code is not valid."}, 401)


# --- account pages ------------------------------------------------------------------------

def _memberships(session, user: User, state: str):
    return list(session.scalars(
        select(Membership).where(Membership.user_id == user.id, Membership.status == state)
        .order_by(Membership.id)).unique())


@router.get("/account")
def account(request: Request, session: SessionDep, user: LoginDep):
    return render(request, "account/index.html", {
        "user": user,
        "two_factor": totp.enabled(session, user.id) is not None,
        "memberships": _memberships(session, user, "active"),
        "pending": _memberships(session, user, "pending"),
    })


@router.post("/account/password", dependencies=[Depends(verify_csrf)])
def change_password(request: Request, session: SessionDep, user: LoginDep,
                    current: Annotated[str, Form()] = "", password: Annotated[str, Form()] = "",
                    confirm: Annotated[str, Form()] = ""):
    err = None if _verify_password(user, current) else "Current password is wrong."
    err = err or passwords.check_new_password(password, confirm, user.email)
    if err:
        flash(request, err, "error")
        return RedirectResponse("/account", 303)
    user.password_hash = hasher.hash(password)
    user.session_version += 1
    events.record_for_user(session, user.id, "password.changed")
    session.commit()
    request.session["sv"] = user.session_version
    flash(request, "Password changed. Other devices were signed out.")
    return RedirectResponse("/account", 303)


@router.post("/account/sign-out-everywhere", dependencies=[Depends(verify_csrf)])
def sign_out_everywhere(request: Request, session: SessionDep, user: LoginDep):
    user.session_version += 1
    events.record_for_user(session, user.id, "session.revoke_all")
    session.commit()
    request.session.clear()
    return RedirectResponse("/login", 303)


@router.get("/account/security")
def security(request: Request, session: SessionDep, user: LoginDep):
    row = session.get(UserTotp, user.id)
    ctx = {"user": user, "required": bool(request.query_params.get("required")),
           "enabled": row is not None and row.enabled_at is not None, "pending": None,
           "remaining": totp.remaining_recovery_codes(session, user.id)}
    if row is not None and row.enabled_at is None:
        secret = totp.decrypt_secret(row.secret_enc)
        ctx["pending"] = {"secret": secret, "qr": totp.qr_data_uri(
            totp.provisioning_uri(secret, user.email))}
    return _no_store(render(request, "account/security.html", ctx))


@router.post("/account/security/2fa/start", dependencies=[Depends(verify_csrf)])
def start_enrolment(request: Request, session: SessionDep, user: LoginDep):
    row = session.get(UserTotp, user.id)
    if row is None or row.enabled_at is None:
        session.execute(delete(UserTotp).where(UserTotp.user_id == user.id))
        session.add(UserTotp(user_id=user.id, secret_enc=totp.encrypt_secret(totp.new_secret())))
        session.commit()
    return RedirectResponse("/account/security", 303)


@router.post("/account/security/2fa/enable", dependencies=[Depends(verify_csrf)])
def enable_two_factor(request: Request, session: SessionDep, user: LoginDep,
                      code: Annotated[str, Form()] = ""):
    row = session.get(UserTotp, user.id)
    if row is None or row.enabled_at is not None:
        return RedirectResponse("/account/security", 303)
    if ratelimit.totp_failures.blocked(str(user.id)) or not totp.verify_code(session, row, code):
        ratelimit.totp_failures.hit(str(user.id))
        flash(request, "That code is not valid. Check the time on your phone and try again.",
              "error")
        session.rollback()
        return RedirectResponse("/account/security", 303)
    row.enabled_at = func.now()
    codes = totp.generate_recovery_codes(session, user.id)
    events.record_for_user(session, user.id, "2fa.enabled")
    session.commit()
    return _no_store(render(request, "account/recovery_codes.html", {"codes": codes}))


@router.post("/account/security/2fa/disable", dependencies=[Depends(verify_csrf)])
def disable_two_factor(request: Request, session: SessionDep, user: LoginDep,
                       password: Annotated[str, Form()] = "", code: Annotated[str, Form()] = ""):
    row = totp.enabled(session, user.id)
    if row is None:
        return RedirectResponse("/account/security", 303)
    required = session.scalar(select(func.count()).select_from(Membership).join(
        Tenant, Tenant.id == Membership.tenant_id).where(
        Membership.user_id == user.id, Membership.status == "active", Tenant.require_2fa))
    if required:
        flash(request, "A workspace you belong to requires two-factor login.", "error")
    elif ratelimit.totp_failures.blocked(str(user.id)):
        flash(request, "Too many attempts. Try again later.", "error")
    elif not _verify_password(user, password) or not totp.verify_code(session, row, code):
        ratelimit.totp_failures.hit(str(user.id))
        flash(request, "Password or code is wrong.", "error")
    else:
        session.execute(delete(UserTotp).where(UserTotp.user_id == user.id))
        session.execute(delete(RecoveryCode).where(RecoveryCode.user_id == user.id))
        events.record_for_user(session, user.id, "2fa.disabled")
        session.commit()
        flash(request, "Two-factor login is off.")
        return RedirectResponse("/account/security", 303)
    session.rollback()
    return RedirectResponse("/account/security", 303)


# --- tenant policy ------------------------------------------------------------------------

@router.post("/settings/require-2fa")
def require_two_factor(request: Request, session: SessionDep, user: ManagerDep,
                       enforce: Annotated[str, Form()] = ""):
    tenant = session.get(Tenant, request.state.tenant.id)
    want = enforce == "on"
    if want and totp.enabled(session, user.id) is None:
        flash(request, "Turn on two-factor login for your own account first.", "error")
        return RedirectResponse("/settings", 303)
    tenant.require_2fa = want
    set_tenant(session, tenant.id)
    audit.record(session, "tenant.require_2fa", user_id=user.id, details={"enabled": want})
    session.commit()
    flash(request, "Two-factor login is now required for everyone in this workspace."
          if want else "Two-factor login is no longer required for this workspace.")
    return RedirectResponse("/settings", 303)
