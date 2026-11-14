import argparse
import getpass
import os
import sys
from pathlib import Path

from sqlalchemy import create_engine, func, select

from ledgerline.accounts import tokens
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


def _date_range(args: argparse.Namespace):
    from datetime import date

    start, end = date.fromisoformat(args.start), date.fromisoformat(args.end)
    if end < start:
        raise SystemExit("error: --to is before --from")
    return start, end


def _tenant_sessions(args: argparse.Namespace):
    from sqlalchemy import select

    from ledgerline.db import for_each_tenant, get_sessionmaker, rls_bypass_reason
    from ledgerline.models import Tenant

    with get_sessionmaker()() as probe:
        known = set(probe.scalars(select(Tenant.id)))
        reason = rls_bypass_reason(probe)
    if reason and not args.allow_rls_bypass:
        raise SystemExit(
            f"error: refusing to run, {reason}, so tenants would not be isolated. Use the "
            "application role (LEDGERLINE_DATABASE_URL), or pass --allow-rls-bypass.")
    if args.tenant is not None and args.tenant not in known:
        raise SystemExit(f"error: no tenant with id {args.tenant}")
    for tid, session in for_each_tenant():
        if args.tenant is None or tid == args.tenant:
            yield tid, session


def pilot_report(args: argparse.Namespace) -> int:
    from ledgerline.platform import pilot

    start, end = _date_range(args)
    for tid, (reports, feedback) in pilot.report_by_tenant(
        _tenant_sessions(args), start, end
    ).items():
        print(pilot.render_text(tid, reports, feedback, start, end), end="\n\n")
    return 0


def feedback_export(args: argparse.Namespace) -> int:
    from ledgerline.platform import pilot

    start, end = _date_range(args)
    rows = []
    for _, session in _tenant_sessions(args):
        rows += pilot.feedback_rows(session, start, end)
    sys.stdout.write(pilot.feedback_csv(rows))
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


def invite_owner(args: argparse.Namespace) -> int:
    """Send the normal invitation mail to the first owner of a tenant (no password, no login)."""
    from ledgerline import mail
    from ledgerline.accounts import invitations
    from ledgerline.platform import validation as v

    address, err = v.check_email(args.email)
    if err or not address:
        print(f"error: {err or '--email is required'}", file=sys.stderr)
        return 1
    address = address.lower()
    if not mail.can_deliver():
        print("error: email is not set up (LEDGERLINE_SMTP_HOST and LEDGERLINE_SMTP_FROM), "
              "so the invitation cannot be sent", file=sys.stderr)
        return 1
    with tenant_session(args.tenant) as session:
        tenant = session.get(Tenant, args.tenant)
        if tenant is None:
            print(f"error: tenant {args.tenant} does not exist", file=sys.stderr)
            return 1
        invitation = invitations.prepare(session, tenant, address, Role.OWNER.value)
        if invitation is None:
            print(f"{address} is already a member of tenant {tenant.id}; nothing sent.")
            return 0
        delivered = invitations.deliver(session, invitation, role=Role.OWNER.value,
                                        actor_id=None, source="cli")
        session.commit()
    if not delivered:
        print(f"error: the invitation email to {address} could not be sent. Check the SMTP "
              "settings and run the command again; it issues a fresh link.", file=sys.stderr)
        return 1
    print(f"Invitation sent to {address} as owner of tenant {args.tenant} "
          f"(the link works for {tokens.INVITE_TTL.days} days).")
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


def _import_context(session, args):
    """The administration and acting user for an import, or None after printing why not."""
    from ledgerline.models import Administration

    admins = list(session.scalars(select(Administration).order_by(Administration.id)))
    if args.administration is not None:
        admins = [a for a in admins if a.id == args.administration]
    if len(admins) != 1:
        print("error: pass --administration (this tenant has "
              f"{len(admins)} matching administrations)", file=sys.stderr)
        return None
    user = None
    if args.user:
        user = session.scalar(select(User).where(func.lower(User.email) == args.user.lower()))
        if user is None:
            print(f"error: no user {args.user}", file=sys.stderr)
            return None
    return admins[0], user


def _print_rows(rows, columns) -> None:
    for r in rows:
        notes = r.reason or ""
        if r.warnings:
            notes = "; ".join(filter(None, [notes, *r.warnings]))
        print(f"row {r.row:>4}  {r.action:<6}  {columns(r)}  {notes}".rstrip())


def import_contacts(args: argparse.Namespace) -> int:
    from ledgerline.imports import contacts as contact_import
    from ledgerline.imports.csvio import CsvError

    content = Path(args.file).read_bytes()
    with get_sessionmaker()() as lookup:
        tenant_id = _resolve_tenant(lookup, args.tenant)
    if tenant_id is None:
        return 1
    with tenant_session(tenant_id) as session:
        context = _import_context(session, args)
        if context is None:
            return 1
        admin, user = context
        try:
            plan = contact_import.plan_contacts(session, admin, content)
        except CsvError as exc:
            print(f"error: {exc}", file=sys.stderr)
            return 1
        for warning in plan.warnings:
            print(f"note: {warning}")
        _print_rows(plan.rows, lambda r: (r.data.get("name") or "")[:40].ljust(40))
        print(f"{plan.count('create')} to create, {plan.count('skip')} duplicates skipped, "
              f"{plan.count('error')} with errors.")
        if not args.apply:
            print("Dry run: nothing was saved. Run again with --apply to create the contacts.")
            return 0
        created = contact_import.apply_plan(session, admin, plan, user.id if user else None,
                                            Path(args.file).name)
        session.commit()
        print(f"Created {created} contacts.")
    return 0


def import_opening_items(args: argparse.Namespace) -> int:
    from ledgerline.imports import opening
    from ledgerline.imports.csvio import CsvError

    content = Path(args.file).read_bytes()
    with get_sessionmaker()() as lookup:
        tenant_id = _resolve_tenant(lookup, args.tenant)
    if tenant_id is None:
        return 1
    with tenant_session(tenant_id) as session:
        context = _import_context(session, args)
        if context is None:
            return 1
        admin, user = context
        try:
            start = opening.resolve_start_date(session, admin, args.start_date or "")
            rows, warnings, _ = opening.plan_csv(session, admin, content, start)
        except (opening.OpeningError, CsvError) as exc:
            print(f"error: {exc}", file=sys.stderr)
            return 1
        for warning in warnings:
            print(f"note: {warning}")
        _print_rows(rows, lambda r: f"{r.kind or '?':<8} {r.number[:20]:<20} "
                                    f"{r.contact_name[:28]:<28} open {r.open_cents / 100:>10.2f}")
        creates = sum(1 for r in rows if r.action == "create")
        errors = sum(1 for r in rows if r.action == "error")
        skipped = sum(1 for r in rows if r.action == "skip")
        print(f"Start date {start}: {creates} to create, {skipped} already imported, "
              f"{errors} with errors.")
        if errors:
            print("error: fix the rows with errors first; nothing was saved.", file=sys.stderr)
            return 1
        if not args.apply:
            print("Dry run: nothing was saved. Run again with --apply to post the items.")
            return 0
        try:
            created = opening.apply_items(session, admin, rows, start, user.id if user else None,
                                          Path(args.file).name)
            session.commit()
        except opening.OpeningError as exc:
            session.rollback()
            print(f"error: {exc}", file=sys.stderr)
            return 1
        print(f"Posted {created} opening items dated {start}.")
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


NO_UPDATE_TABLES = {"feedback_entries"}


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
            sql.SQL("GRANT SELECT, INSERT, {}DELETE ON {} TO ledgerline_app").format(
                sql.SQL("" if table in NO_UPDATE_TABLES else "UPDATE, "), sql.Identifier(table)
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

    inv = sub.add_parser("invite-owner",
                         help="email the first owner of a tenant the normal invitation link")
    inv.add_argument("--tenant", type=int, required=True)
    inv.add_argument("--email", required=True)
    inv.set_defaults(func=invite_owner)

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

    for name, handler, blurb in (
        ("pilot-report", pilot_report, "uploads, processing time, feedback for a date range"),
        ("feedback-export", feedback_export, "feedback entries as CSV (verbatim)"),
    ):
        cmd = sub.add_parser(name, help=blurb)
        cmd.add_argument("--from", dest="start", required=True, help="YYYY-MM-DD, inclusive")
        cmd.add_argument("--to", dest="end", required=True, help="YYYY-MM-DD, inclusive")
        cmd.add_argument("--tenant", type=int, help="limit to one tenant")
        cmd.add_argument("--allow-rls-bypass", action="store_true",
                         help="run even if the database role bypasses row-level security")
        cmd.set_defaults(func=handler)

    imp = sub.add_parser("import-contacts",
                         help="import customers and suppliers from a CSV file (dry run by default)")
    imp.add_argument("--file", required=True)
    imp.add_argument("--tenant", type=int, help="tenant id (optional with a single tenant)")
    imp.add_argument("--administration", type=int, help="administration id (optional if only one)")
    imp.add_argument("--user", help="email of the user to record in the audit trail")
    imp.add_argument("--apply", action="store_true", help="save instead of only previewing")
    imp.set_defaults(func=import_contacts)

    items = sub.add_parser("import-opening-items",
                           help="import unpaid invoices open on the start date from a CSV file "
                                "(dry run by default)")
    items.add_argument("--file", required=True)
    items.add_argument("--start-date", help="yyyy-mm-dd; optional once opening figures exist")
    items.add_argument("--tenant", type=int, help="tenant id (optional with a single tenant)")
    items.add_argument("--administration", type=int,
                       help="administration id (optional if only one)")
    items.add_argument("--user", help="email of the user to record in the audit trail")
    items.add_argument("--apply", action="store_true", help="save instead of only previewing")
    items.set_defaults(func=import_opening_items)

    work = sub.add_parser("worker", help="read incoming documents and poll the invoice mailbox")
    work.set_defaults(func=worker)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
