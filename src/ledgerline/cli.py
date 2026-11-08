import argparse
import getpass
import os
import sys
from pathlib import Path

from sqlalchemy import create_engine, func, select

from ledgerline.auth import hash_password
from ledgerline.config import get_settings
from ledgerline.db import get_sessionmaker, tenant_session
from ledgerline.models import Membership, Role, Tenant, User


def _password() -> str:
    password = os.environ.get("LEDGERLINE_INITIAL_PASSWORD")
    if password:
        return password
    first = getpass.getpass("Password (min 12 characters): ")
    if first != getpass.getpass("Repeat password: "):
        raise SystemExit("Passwords do not match.")
    return first


def _resolve_tenant(session, tenant_id: int | None) -> int | None:
    """The given tenant, or the only one when the installation has exactly one."""
    if tenant_id is not None:
        if session.get(Tenant, tenant_id) is None:
            print(f"error: tenant {tenant_id} does not exist", file=sys.stderr)
            return None
        return tenant_id
    ids = list(session.scalars(select(Tenant.id)))
    if len(ids) != 1:
        print(f"error: pass --tenant (this installation has {len(ids)} tenants)", file=sys.stderr)
        return None
    return ids[0]


def create_tenant(args: argparse.Namespace) -> int:
    with get_sessionmaker()() as session:
        tenant = Tenant(name=args.name.strip())
        session.add(tenant)
        session.commit()
        print(f"Created tenant {tenant.id}: {tenant.name}")
    return 0


def delete_tenant(args: argparse.Namespace) -> int:
    from sqlalchemy.orm import Session

    from ledgerline.platform import offboarding

    engine = create_engine(get_settings().owner_database_url)
    try:
        with engine.connect() as conn:
            name = offboarding.tenant_name(conn, args.tenant)
        if name is None:
            print(f"error: tenant {args.tenant} does not exist", file=sys.stderr)
            return 1
        if args.confirm != name:
            print(f"error: --confirm must be the tenant's exact name: {name!r}", file=sys.stderr)
            return 1
        with Session(engine) as session:
            exports = offboarding.export_tenant(args.tenant, Path(args.export_dir), session)
        with engine.begin() as conn:
            report = offboarding.delete_tenant(conn, args.tenant)
    except offboarding.OffboardingError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    finally:
        engine.dispose()
    for path in exports:
        print(f"exported {path}")
    total = sum(report.deleted_rows.values())
    print(f"Deleted tenant {args.tenant} ({name}): {total} rows, {report.deleted_users} logins.")
    print("Backups still hold this data until their retention ends; see docs/OPERATIONS.md.")
    return 0


def create_user(args: argparse.Namespace) -> int:
    email = args.email.strip().lower()
    try:
        password_hash = hash_password(_password())
    except ValueError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    with get_sessionmaker()() as session:
        tenant_id = _resolve_tenant(session, args.tenant)
        if tenant_id is None:
            return 1
        if session.scalar(select(User.id).where(func.lower(User.email) == email)):
            print(f"error: user {email} already exists", file=sys.stderr)
            return 1
        user = User(email=email, password_hash=password_hash)
        session.add(user)
        session.flush()
        session.add(Membership(user_id=user.id, tenant_id=tenant_id, role=args.role))
        session.commit()
    print(f"Created {args.role} {email} in tenant {tenant_id}")
    return 0


def add_membership(args: argparse.Namespace) -> int:
    with get_sessionmaker()() as session:
        user = session.scalar(select(User).where(func.lower(User.email) == args.email.lower()))
        tenant_id = _resolve_tenant(session, args.tenant)
        if tenant_id is None:
            return 1
        if user is None:
            print(f"error: user {args.email} not found", file=sys.stderr)
            return 1
        if session.scalar(
            select(Membership.id).where(
                Membership.user_id == user.id, Membership.tenant_id == tenant_id
            )
        ):
            print("error: already a member", file=sys.stderr)
            return 1
        session.add(Membership(user_id=user.id, tenant_id=tenant_id, role=args.role))
        session.commit()
    print(f"Added {args.email} to tenant {tenant_id} as {args.role}")
    return 0


def create_administration(args: argparse.Namespace) -> int:
    from ledgerline.ledger.chart import create_administration as create

    with get_sessionmaker()() as lookup:
        tenant_id = _resolve_tenant(lookup, args.tenant)
    if tenant_id is None:
        return 1
    with tenant_session(tenant_id) as session:
        admin = create(session, name=args.name, legal_form=args.legal_form)
        session.commit()
        print(f"Created administration {admin.id}: {admin.name} (tenant {tenant_id})")
    return 0


ROLE_SETUP = """
DO $$ BEGIN
    IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'ledgerline_app') THEN
        CREATE ROLE ledgerline_app NOLOGIN;
    END IF;
END $$;
ALTER ROLE ledgerline_app NOSUPERUSER NOBYPASSRLS NOCREATEDB NOCREATEROLE;
GRANT USAGE ON SCHEMA public TO ledgerline_app;
REVOKE ALL ON ALL TABLES IN SCHEMA public FROM ledgerline_app;
REVOKE ALL ON ALL SEQUENCES IN SCHEMA public FROM ledgerline_app;
"""
SHARED_TABLES = (
    "users", "tenants", "memberships", "auth_tokens", "user_totp", "recovery_codes",
)
PROTECTED_TABLES = """
SELECT c.relname FROM pg_class c
WHERE c.relnamespace = 'public'::regnamespace AND c.relkind = 'r'
  AND c.relrowsecurity AND c.relforcerowsecurity
  AND EXISTS (SELECT 1 FROM pg_policy p WHERE p.polrelid = c.oid AND p.polname = 'tenant_isolation')
"""
OWNED_SEQUENCES = """
SELECT s.relname FROM pg_class s
JOIN pg_depend d ON d.objid = s.oid AND d.classid = 'pg_class'::regclass
     AND d.refclassid = 'pg_class'::regclass AND d.deptype IN ('a', 'i')
JOIN pg_class t ON t.oid = d.refobjid
WHERE s.relkind = 'S' AND t.relnamespace = 'public'::regnamespace AND t.relname = ANY(%s)
"""
APP_FUNCTIONS = """
SELECT p.oid::regprocedure::text FROM pg_proc p
WHERE p.pronamespace = 'public'::regnamespace AND p.proname LIKE 'ledgerline\\_%'
"""


def grant_app_role(raw) -> list[str]:
    """Grant the app role data access on protected tables only; return the tables granted.

    A table counts as protected when row-level security is enabled and forced and it carries a
    tenant_isolation policy. Every other table stays closed (fail closed).
    """
    from psycopg import sql

    raw.execute(ROLE_SETUP)
    protected = [r[0] for r in raw.execute(PROTECTED_TABLES).fetchall()]
    tables = sorted({*protected, *SHARED_TABLES})
    for table in tables:
        raw.execute(
            sql.SQL("GRANT SELECT, INSERT, UPDATE, DELETE ON {} TO ledgerline_app").format(
                sql.Identifier(table)
            )
        )
    for (sequence,) in raw.execute(OWNED_SEQUENCES, (tables,)).fetchall():
        raw.execute(
            sql.SQL("GRANT USAGE, SELECT ON SEQUENCE {} TO ledgerline_app").format(
                sql.Identifier(sequence)
            )
        )
    for (function,) in raw.execute(APP_FUNCTIONS).fetchall():
        raw.execute(
            sql.SQL("GRANT EXECUTE ON FUNCTION {} TO ledgerline_app").format(sql.SQL(function))
        )
    return tables


def ensure_app_role(_args: argparse.Namespace) -> int:
    """Give the ledgerline_app role its login password and grants. Run as owner, after `migrate`.

    Grants are recomputed from the catalog on every run, so a database restored on a fresh server
    works and a table without a forced tenant policy stays inaccessible to the app role.
    """
    from psycopg import sql

    settings = get_settings()
    if not settings.app_db_password:
        print("error: LEDGERLINE_APP_DB_PASSWORD is not set", file=sys.stderr)
        return 1
    engine = create_engine(settings.owner_database_url)
    try:
        with engine.begin() as conn:
            raw = conn.connection.driver_connection
            grant_app_role(raw)
            raw.execute(
                sql.SQL("ALTER ROLE ledgerline_app LOGIN PASSWORD {}").format(
                    sql.Literal(settings.app_db_password)
                )
            )
    finally:
        engine.dispose()
    print("ledgerline_app role is ready.")
    return 0


def migrate(args: argparse.Namespace) -> int:
    from alembic import command
    from alembic.config import Config

    config = Config(str(Path(args.config)))
    command.upgrade(config, "head")
    return 0


def seed_demo(args: argparse.Namespace) -> int:
    from ledgerline import demo

    with get_sessionmaker()() as session:
        user = None
        if args.email:
            user = session.scalar(select(User).where(func.lower(User.email) == args.email.lower()))
            if user is None:
                print(f"error: user {args.email} not found", file=sys.stderr)
                return 1
        existing = session.scalar(select(Tenant.id).where(Tenant.name == demo.DEMO_NAME))
        if existing:
            print(f"error: demo data is already loaded (tenant {existing})", file=sys.stderr)
            return 1
        tenant = Tenant(name=demo.DEMO_NAME)
        session.add(tenant)
        session.flush()
        if user is not None:
            session.add(Membership(user_id=user.id, tenant_id=tenant.id, role=Role.OWNER))
        session.commit()
        tenant_id = tenant.id
    with tenant_session(tenant_id) as session:
        demo.seed(session)
        session.commit()
    print(f"Demo data loaded into tenant {tenant_id}.")
    if user is None:
        print(
            f"No user can see it yet: ledgerline add-membership --email <address> "
            f"--tenant {tenant_id} --role owner"
        )
    return 0


def worker(_args: argparse.Namespace) -> int:
    from ledgerline.inbox import worker as inbox_worker

    return inbox_worker.main()


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="ledgerline")
    sub = parser.add_subparsers(dest="command", required=True)

    tenant = sub.add_parser("create-tenant", help="create a tenant (a customer account)")
    tenant.add_argument("--name", required=True)
    tenant.set_defaults(func=create_tenant)

    drop = sub.add_parser("delete-tenant", help="export, then delete a tenant and all its data")
    drop.add_argument("--tenant", type=int, required=True)
    drop.add_argument("--confirm", required=True, help="the tenant's exact name")
    drop.add_argument("--export-dir", required=True, help="where the export ZIPs are written")
    drop.set_defaults(func=delete_tenant)

    roles = [r.value for r in Role]
    user = sub.add_parser("create-user", help="create a user with a membership in a tenant")
    user.add_argument("--email", required=True)
    user.add_argument("--role", choices=roles, default=Role.OWNER.value)
    user.add_argument("--tenant", type=int, help="tenant id (optional with a single tenant)")
    user.set_defaults(func=create_user)

    member = sub.add_parser("add-membership", help="give an existing user access to a tenant")
    member.add_argument("--email", required=True)
    member.add_argument("--role", choices=roles, default=Role.OWNER.value)
    member.add_argument("--tenant", type=int, help="tenant id (optional with a single tenant)")
    member.set_defaults(func=add_membership)

    admin = sub.add_parser("create-administration", help="create an administration")
    admin.add_argument("--name", required=True)
    admin.add_argument("--legal-form", default="eenmanszaak")
    admin.add_argument("--tenant", type=int, help="tenant id (optional with a single tenant)")
    admin.set_defaults(func=create_administration)

    role = sub.add_parser(
        "ensure-app-role", help="set the login password of the ledgerline_app database role"
    )
    role.set_defaults(func=ensure_app_role)

    mig = sub.add_parser("migrate", help="apply database migrations")
    mig.add_argument("--config", default="alembic.ini")
    mig.set_defaults(func=migrate)

    seed = sub.add_parser("seed-demo", help="load demo data into a new tenant")
    seed.add_argument("--email", help="existing user to make owner of the demo tenant")
    seed.set_defaults(func=seed_demo)

    work = sub.add_parser("worker", help="read incoming documents and poll the invoice mailbox")
    work.set_defaults(func=worker)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
