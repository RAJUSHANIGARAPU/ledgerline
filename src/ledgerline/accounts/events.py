"""Audit events about a person, recorded in every tenant they belong to (never secrets)."""
from sqlalchemy import select
from sqlalchemy.orm import Session

from ledgerline import audit
from ledgerline.db import set_tenant
from ledgerline.models import Membership


def record_for_user(session: Session, user_id: int, action: str,
                    details: dict | None = None, *, only_tenant: int | None = None) -> None:
    query = select(Membership.tenant_id).where(
        Membership.user_id == user_id, Membership.status == "active")
    if only_tenant is not None:
        query = query.where(Membership.tenant_id == only_tenant)
    tenant_ids = list(session.scalars(query))
    previous = session.info.get("tenant_id")
    try:
        for tenant_id in tenant_ids:
            set_tenant(session, tenant_id)
            audit.record(session, action, user_id=user_id, details=details)
            session.flush()
    finally:
        set_tenant(session, previous)
