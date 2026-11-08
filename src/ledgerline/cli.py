import argparse
import getpass
import os
import sys
from pathlib import Path

from sqlalchemy import func, select

from ledgerline.auth import hash_password
from ledgerline.db import get_sessionmaker
from ledgerline.models import Role, User


def _password() -> str:
    password = os.environ.get("LEDGERLINE_INITIAL_PASSWORD")
    if password:
        return password
    first = getpass.getpass("Password (min 12 characters): ")
    if first != getpass.getpass("Repeat password: "):
        raise SystemExit("Passwords do not match.")
    return first


def create_user(args: argparse.Namespace) -> int:
    email = args.email.strip().lower()
    try:
        password_hash = hash_password(_password())
    except ValueError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    with get_sessionmaker()() as session:
        if session.scalar(select(User.id).where(func.lower(User.email) == email)):
            print(f"error: user {email} already exists", file=sys.stderr)
            return 1
        session.add(User(email=email, password_hash=password_hash, role=args.role))
        session.commit()
    print(f"Created {args.role} {email}")
    return 0


def create_administration(args: argparse.Namespace) -> int:
    from ledgerline.ledger.chart import create_administration as create

    with get_sessionmaker()() as session:
        admin = create(session, name=args.name, legal_form=args.legal_form)
        session.commit()
        print(f"Created administration {admin.id}: {admin.name}")
    return 0


def migrate(args: argparse.Namespace) -> int:
    from alembic import command
    from alembic.config import Config

    config = Config(str(Path(args.config)))
    command.upgrade(config, "head")
    return 0


def seed_demo(_args: argparse.Namespace) -> int:
    from ledgerline import demo

    with get_sessionmaker()() as session:
        demo.seed(session)
        session.commit()
    print("Demo data loaded.")
    return 0


def worker(_args: argparse.Namespace) -> int:
    from ledgerline.inbox import worker as inbox_worker

    return inbox_worker.main()


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="ledgerline")
    sub = parser.add_subparsers(dest="command", required=True)

    user = sub.add_parser("create-user", help="create a user")
    user.add_argument("--email", required=True)
    user.add_argument("--role", choices=[r.value for r in Role], default=Role.OWNER.value)
    user.set_defaults(func=create_user)

    admin = sub.add_parser("create-administration", help="create an administration")
    admin.add_argument("--name", required=True)
    admin.add_argument("--legal-form", default="eenmanszaak")
    admin.set_defaults(func=create_administration)

    mig = sub.add_parser("migrate", help="apply database migrations")
    mig.add_argument("--config", default="alembic.ini")
    mig.set_defaults(func=migrate)

    seed = sub.add_parser("seed-demo", help="load demo data")
    seed.set_defaults(func=seed_demo)

    work = sub.add_parser("worker", help="read incoming documents and poll the invoice mailbox")
    work.set_defaults(func=worker)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
