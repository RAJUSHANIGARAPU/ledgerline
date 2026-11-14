"""Invite a person into a tenant: membership, single-use token, the mail, and the audit trail.

Shared by the in-app invite (owner) and the operator CLI (`ledgerline invite-owner`), so both
issue the same token with the same expiry, send the same mail and audit the same way.
"""
from dataclasses import dataclass

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ledgerline import audit, mail
from ledgerline.accounts import passwords, tokens
from ledgerline.config import get_settings
from ledgerline.models import AuditEvent, Membership, Tenant, User

SENT = "invite.sent"
FAILED = "invite.mail_failed"


@dataclass(frozen=True)
class Invitation:
    membership_id: int
    address: str
    content: mail.Content
    link: str


def _base_url() -> str:
    return get_settings().public_base_url.rstrip("/")


def _build(session: Session, tenant: Tenant, target: User, membership: Membership) -> Invitation:
    if target.is_active:
        link = f"{_base_url()}/login"
        content = mail.invite_existing_user(tenant.name, link)
    else:
        tokens.invalidate_invites(session, membership.id)
        token = tokens.issue(session, target.id, "invite", membership.id)
        link = f"{_base_url()}/invite/{token}"
        content = mail.invite_new_user(tenant.name, link, tokens.INVITE_TTL.days)
    return Invitation(membership.id, target.email, content, link)


def prepare(session: Session, tenant: Tenant, address: str, role: str) -> Invitation | None:
    """Create the pending login and membership if needed. None when already a member."""
    address = address.strip().lower()
    target = session.scalar(select(User).where(func.lower(User.email) == address))
    if target is None:
        target = User(email=address, password_hash=passwords.UNUSABLE, is_active=False)
        session.add(target)
        session.flush()
    membership = session.scalar(select(Membership).where(
        Membership.user_id == target.id, Membership.tenant_id == tenant.id))
    if membership is None:
        membership = Membership(user_id=target.id, tenant_id=tenant.id, role=role,
                                status="pending")
        session.add(membership)
        session.flush()
    if membership.status != "pending":
        return None
    return _build(session, tenant, target, membership)


def prepare_resend(session: Session, tenant: Tenant, user_id: int) -> Invitation | None:
    """A fresh link for a pending invitation of this tenant, or None when there is none."""
    membership = session.scalar(select(Membership).where(
        Membership.user_id == user_id, Membership.tenant_id == tenant.id,
        Membership.status == "pending"))
    if membership is None:
        return None
    return _build(session, tenant, session.get(User, user_id), membership)


def deliver(session: Session, invitation: Invitation, *, role: str, actor_id: int | None,
            source: str) -> bool:
    """Send the mail and audit the real outcome. The caller commits."""
    # Without SMTP, development mode writes the link to the log; that counts as delivered.
    ok = mail.send(invitation.address, invitation.content, invitation.link) \
        or not mail.is_configured()
    audit.record(session, SENT if ok else FAILED, user_id=actor_id, entity_type="membership",
                 entity_id=invitation.membership_id, details={"role": role, "source": source})
    return ok


def failed_memberships(session: Session, membership_ids: list[int]) -> set[int]:
    """Memberships whose most recent invitation mail failed."""
    if not membership_ids:
        return set()
    rows = session.execute(
        select(AuditEvent.entity_id, AuditEvent.action).where(
            AuditEvent.entity_type == "membership", AuditEvent.entity_id.in_(membership_ids),
            AuditEvent.action.in_((SENT, FAILED))).order_by(AuditEvent.id)).all()
    latest = {entity_id: action for entity_id, action in rows}
    return {i for i, action in latest.items() if action == FAILED}
