"""Engine, sessions and tenant context (docs/adr/0001-multi-tenancy.md).

The application connects as `ledgerline_app`, a role without BYPASSRLS, so the row-level
security policies always apply. Every transaction starts with the session's tenant id copied
into the transaction-local setting `app.tenant_id`; without it, queries on tenant tables raise.
"""
from collections.abc import Iterator
from contextlib import contextmanager
from functools import lru_cache

from sqlalchemy import Engine, create_engine, event, select, text
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from ledgerline.config import get_settings


class Base(DeclarativeBase):
    pass


@lru_cache
def get_engine() -> Engine:
    return create_engine(get_settings().database_url, pool_pre_ping=True)


@lru_cache
def get_sessionmaker() -> sessionmaker[Session]:
    return sessionmaker(get_engine(), expire_on_commit=False)


_SET_TENANT = text("SELECT set_config('app.tenant_id', :tenant, true)")


@event.listens_for(Session, "after_begin")
def _apply_tenant(session: Session, _transaction, connection) -> None:
    tenant_id = session.info.get("tenant_id")
    if tenant_id is not None:
        connection.execute(_SET_TENANT, {"tenant": str(int(tenant_id))})


def set_tenant(session: Session, tenant_id: int | None) -> None:
    """Switch the session's tenant, also for a transaction that has already started."""
    if tenant_id is None:
        session.info.pop("tenant_id", None)
        return
    session.info["tenant_id"] = int(tenant_id)
    if session.in_transaction():
        session.execute(_SET_TENANT, {"tenant": str(int(tenant_id))})


@contextmanager
def tenant_session(tenant_id: int) -> Iterator[Session]:
    with get_sessionmaker()() as session:
        set_tenant(session, tenant_id)
        yield session


def for_each_tenant(
    factory: sessionmaker[Session] | None = None,
) -> Iterator[tuple[int, Session]]:
    """Yield a session scoped to each tenant in turn, for background work that spans tenants."""
    from ledgerline.models import Tenant

    factory = factory or get_sessionmaker()
    with factory() as listing:
        tenant_ids = list(listing.scalars(select(Tenant.id).order_by(Tenant.id)))
    for tenant_id in tenant_ids:
        with factory() as session:
            set_tenant(session, tenant_id)
            yield tenant_id, session


def get_session() -> Iterator[Session]:
    with get_sessionmaker()() as session:
        yield session
