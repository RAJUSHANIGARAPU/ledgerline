"""tenancy hardening: unguessable inbox address, composite foreign keys, fail-closed grants

See docs/adr/0001-multi-tenancy.md, "Implementation notes". Replaces the numeric email routing
by a random per-administration token, makes every reference between tenant tables carry the
tenant (so the database refuses a row that points at another tenant's row), hardens the
SECURITY DEFINER functions and stops granting privileges on tables created in the future.

Revision ID: 0005
Revises: 0004
Create Date: 2026-11-08
"""
import sqlalchemy as sa
from alembic import op

revision = "0005"
down_revision = "0004"
branch_labels = None
depends_on = None

APP_ROLE = "ledgerline_app"

TENANT_TABLES = (
    "administrations",
    "accounts",
    "contacts",
    "journal_entries",
    "journal_lines",
    "number_sequences",
    "attachments",
    "sales_invoices",
    "sales_invoice_lines",
    "purchase_invoices",
    "purchase_invoice_lines",
    "bank_accounts",
    "bank_statements",
    "bank_transactions",
    "payment_allocations",
    "audit_events",
    "inbox_documents",
    "api_tokens",
)
_IN_SCOPE = ", ".join(f"'{t}'" for t in TENANT_TABLES)
SEARCH_PATH = "pg_catalog, public, pg_temp"
ACTIONS = {"a": "NO ACTION", "r": "RESTRICT", "c": "CASCADE", "n": "SET NULL", "d": "SET DEFAULT"}

_SINGLE_FKS = f"""
    SELECT c.conname, c.conrelid::regclass::text AS tbl, a.attname AS col,
           c.confrelid::regclass::text AS ref, af.attname AS refcol,
           c.confdeltype AS on_delete, c.confupdtype AS on_update
    FROM pg_constraint c
    JOIN pg_attribute a ON a.attrelid = c.conrelid AND a.attnum = c.conkey[1]
    JOIN pg_attribute af ON af.attrelid = c.confrelid AND af.attnum = c.confkey[1]
    WHERE c.contype = 'f' AND array_length(c.conkey, 1) = 1
      AND c.connamespace = 'public'::regnamespace
      AND c.conrelid::regclass::text IN ({_IN_SCOPE})
      AND c.confrelid::regclass::text IN ({_IN_SCOPE})
"""
_COMPOSITE_FKS = f"""
    SELECT c.conname, c.conrelid::regclass::text AS tbl, a.attname AS col,
           c.confrelid::regclass::text AS ref, af.attname AS refcol,
           c.confdeltype AS on_delete, c.confupdtype AS on_update
    FROM pg_constraint c
    JOIN pg_attribute a ON a.attrelid = c.conrelid AND a.attnum = c.conkey[2]
    JOIN pg_attribute af ON af.attrelid = c.confrelid AND af.attnum = c.confkey[2]
    WHERE c.contype = 'f' AND array_length(c.conkey, 1) = 2
      AND c.connamespace = 'public'::regnamespace
      AND (SELECT attname FROM pg_attribute
           WHERE attrelid = c.conrelid AND attnum = c.conkey[1]) = 'tenant_id'
      AND c.conrelid::regclass::text IN ({_IN_SCOPE})
      AND c.confrelid::regclass::text IN ({_IN_SCOPE})
"""


def _unique_name(table: str) -> str:
    return f"uq_{table}_tenant_id_id"


def _action(code: str, column: str) -> str:
    """Referential action; SET NULL/DEFAULT must not touch the tenant column."""
    if code in ("n", "d"):
        return f"{ACTIONS[code]} ({column})"
    return ACTIONS[code]


def _composite_foreign_keys() -> None:
    bind = op.get_bind()
    fks = bind.execute(sa.text(_SINGLE_FKS)).mappings().all()
    for fk in fks:
        assert fk["refcol"] == "id", fk
    for table in sorted({fk["ref"] for fk in fks}):
        op.execute(f"ALTER TABLE {table} ADD CONSTRAINT {_unique_name(table)} "
                   "UNIQUE (tenant_id, id)")
    for fk in fks:
        op.execute(f"ALTER TABLE {fk['tbl']} DROP CONSTRAINT {fk['conname']}")
        op.execute(
            f"ALTER TABLE {fk['tbl']} ADD CONSTRAINT {fk['conname']} "
            f"FOREIGN KEY (tenant_id, {fk['col']}) REFERENCES {fk['ref']} (tenant_id, id) "
            f"ON DELETE {_action(fk['on_delete'], fk['col'])} "
            f"ON UPDATE {ACTIONS[fk['on_update']]}"
        )


def _single_column_foreign_keys() -> None:
    bind = op.get_bind()
    for fk in bind.execute(sa.text(_COMPOSITE_FKS)).mappings().all():
        op.execute(f"ALTER TABLE {fk['tbl']} DROP CONSTRAINT {fk['conname']}")
        op.execute(
            f"ALTER TABLE {fk['tbl']} ADD CONSTRAINT {fk['conname']} "
            f"FOREIGN KEY ({fk['col']}) REFERENCES {fk['ref']} ({fk['refcol']}) "
            f"ON DELETE {_action(fk['on_delete'], fk['col'])} "
            f"ON UPDATE {ACTIONS[fk['on_update']]}"
        )
    for table in TENANT_TABLES:
        op.execute(f"ALTER TABLE {table} DROP CONSTRAINT IF EXISTS {_unique_name(table)}")


def upgrade() -> None:
    op.execute(
        "ALTER TABLE administrations ADD COLUMN inbox_token varchar(64) NOT NULL "
        "DEFAULT left(replace(gen_random_uuid()::text || gen_random_uuid()::text, '-', ''), 32)"
    )
    op.create_unique_constraint("uq_administrations_inbox_token", "administrations",
                                ["inbox_token"])
    op.create_check_constraint("ck_administrations_inbox_token", "administrations",
                               "inbox_token ~ '^[a-z0-9]{20,64}$'")

    _composite_foreign_keys()

    op.execute(
        f"""
        CREATE FUNCTION ledgerline_route_inbox_token(token text)
        RETURNS TABLE (administration_id integer, tenant_id integer)
        LANGUAGE sql STABLE SECURITY DEFINER SET search_path = {SEARCH_PATH} AS $$
            SELECT a.id, a.tenant_id FROM public.administrations a WHERE a.inbox_token = $1
        $$;
        CREATE OR REPLACE FUNCTION ledgerline_tenant_of_api_token(token_sha256 text) RETURNS integer
        LANGUAGE sql STABLE SECURITY DEFINER SET search_path = {SEARCH_PATH} AS $$
            SELECT t.tenant_id FROM public.api_tokens t WHERE t.token_sha256 = $1
        $$;
        REVOKE ALL ON FUNCTION ledgerline_route_inbox_token(text) FROM PUBLIC;
        GRANT EXECUTE ON FUNCTION ledgerline_route_inbox_token(text) TO {APP_ROLE};
        DROP FUNCTION ledgerline_tenant_of_administration(integer);

        ALTER DEFAULT PRIVILEGES IN SCHEMA public
            REVOKE SELECT, INSERT, UPDATE, DELETE ON TABLES FROM {APP_ROLE};
        ALTER DEFAULT PRIVILEGES IN SCHEMA public REVOKE USAGE, SELECT ON SEQUENCES FROM {APP_ROLE};
        ALTER DEFAULT PRIVILEGES IN SCHEMA public REVOKE EXECUTE ON FUNCTIONS FROM {APP_ROLE};
        """
    )


def downgrade() -> None:
    op.execute(
        f"""
        ALTER DEFAULT PRIVILEGES IN SCHEMA public
            GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO {APP_ROLE};
        ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT USAGE, SELECT ON SEQUENCES TO {APP_ROLE};
        ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT EXECUTE ON FUNCTIONS TO {APP_ROLE};

        CREATE FUNCTION ledgerline_tenant_of_administration(admin_id integer) RETURNS integer
        LANGUAGE sql STABLE SECURITY DEFINER SET search_path = pg_catalog, public AS $$
            SELECT tenant_id FROM administrations WHERE id = admin_id
        $$;
        CREATE OR REPLACE FUNCTION ledgerline_tenant_of_api_token(token_sha256 text) RETURNS integer
        LANGUAGE sql STABLE SECURITY DEFINER SET search_path = pg_catalog, public AS $$
            SELECT tenant_id FROM api_tokens WHERE api_tokens.token_sha256 = $1
        $$;
        REVOKE ALL ON FUNCTION ledgerline_tenant_of_administration(integer) FROM PUBLIC;
        GRANT EXECUTE ON FUNCTION ledgerline_tenant_of_administration(integer) TO {APP_ROLE};
        DROP FUNCTION ledgerline_route_inbox_token(text);
        """
    )

    _single_column_foreign_keys()

    op.drop_column("administrations", "inbox_token")
