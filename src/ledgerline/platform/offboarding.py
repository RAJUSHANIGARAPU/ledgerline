"""Ending a client: export every administration, then delete the tenant and its data.

Runs as the owner role. The append-only bookkeeping triggers refuse deletes by design, so the
delete runs with session_replication_role = replica, which also skips foreign-key triggers;
every dependent row is therefore deleted explicitly and verified afterwards.
"""
from dataclasses import dataclass, field
from pathlib import Path

from psycopg import sql
from sqlalchemy import Connection, select, text
from sqlalchemy.orm import Session

from ledgerline.db import tenant_session
from ledgerline.models import Administration, Tenant
from ledgerline.platform.gdpr import build_export_zip

TENANT_TABLES = text(
    "SELECT c.relname FROM pg_class c "
    "JOIN pg_attribute a ON a.attrelid = c.oid AND a.attname = 'tenant_id' AND NOT a.attisdropped "
    "WHERE c.relnamespace = 'public'::regnamespace AND c.relkind = 'r' "
    "AND c.relname <> 'memberships' ORDER BY c.relname"
)


class OffboardingError(RuntimeError):
    pass


@dataclass
class DeletionReport:
    tenant_id: int
    exports: list[Path] = field(default_factory=list)
    deleted_rows: dict[str, int] = field(default_factory=dict)
    deleted_users: int = 0


def export_tenant(tenant_id: int, export_dir: Path, session: Session | None = None) -> list[Path]:
    """One ZIP per administration, written before anything is deleted.

    Filters by tenant explicitly: the owner role used for offboarding bypasses row-level security.
    """
    if session is None:
        with tenant_session(tenant_id) as scoped:
            return export_tenant(tenant_id, export_dir, scoped)
    export_dir.mkdir(parents=True, exist_ok=True)
    paths = []
    admins = select(Administration).where(Administration.tenant_id == tenant_id)
    for admin in session.scalars(admins.order_by(Administration.id)):
        path = export_dir / f"tenant-{tenant_id}-administration-{admin.id}.zip"
        spool = build_export_zip(session, admin)
        path.write_bytes(spool.read())
        if path.stat().st_size == 0:
            raise OffboardingError(f"export {path} is empty")
        paths.append(path)
    return paths


def delete_tenant(conn: Connection, tenant_id: int) -> DeletionReport:
    """Delete everything of one tenant inside the caller's transaction (owner connection)."""
    is_owner = conn.execute(text("SELECT rolsuper FROM pg_roles WHERE rolname = current_user"))
    if not is_owner.scalar():
        raise OffboardingError("deleting a tenant needs the owner (superuser) database role")
    report = DeletionReport(tenant_id)
    conn.execute(text("SET LOCAL session_replication_role = replica"))
    raw = conn.connection.driver_connection
    tables = list(conn.execute(TENANT_TABLES).scalars())
    for table in tables:
        result = raw.execute(
            sql.SQL("DELETE FROM {} WHERE tenant_id = %s").format(sql.Identifier(table)),
            (tenant_id,),
        )
        report.deleted_rows[table] = result.rowcount

    members = list(conn.execute(
        text("SELECT user_id FROM memberships WHERE tenant_id = :t"), {"t": tenant_id}).scalars())
    conn.execute(text(
        "DELETE FROM auth_tokens WHERE membership_id IN "
        "(SELECT id FROM memberships WHERE tenant_id = :t)"), {"t": tenant_id})
    report.deleted_rows["memberships"] = conn.execute(
        text("DELETE FROM memberships WHERE tenant_id = :t"), {"t": tenant_id}).rowcount
    orphans = list(conn.execute(text(
        "SELECT u.id FROM users u WHERE u.id = ANY(:ids) "
        "AND NOT EXISTS (SELECT 1 FROM memberships m WHERE m.user_id = u.id)"),
        {"ids": members}).scalars())
    for table in ("auth_tokens", "user_totp", "recovery_codes"):
        raw.execute(
            sql.SQL("DELETE FROM {} WHERE user_id = ANY(%s)").format(sql.Identifier(table)),
            (orphans,),
        )
    report.deleted_users = conn.execute(
        text("DELETE FROM users WHERE id = ANY(:ids)"), {"ids": orphans}).rowcount
    if not conn.execute(text("DELETE FROM tenants WHERE id = :t"), {"t": tenant_id}).rowcount:
        raise OffboardingError(f"tenant {tenant_id} does not exist")

    for table in tables:
        left = raw.execute(
            sql.SQL("SELECT count(*) FROM {} WHERE tenant_id = %s").format(sql.Identifier(table)),
            (tenant_id,),
        ).fetchone()[0]
        if left:
            raise OffboardingError(f"{left} rows of tenant {tenant_id} remain in {table}")
    return report


def tenant_name(conn: Connection, tenant_id: int) -> str | None:
    return conn.execute(select(Tenant.name).where(Tenant.id == tenant_id)).scalar()
