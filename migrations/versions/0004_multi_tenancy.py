"""multi-tenancy: tenants, memberships, tenant_id on every books table, row-level security

See docs/adr/0001-multi-tenancy.md. Existing data moves into tenant 1 and every user becomes a
member of it with their old role. The downgrade folds everything back into a single-tenant
schema: users.role is restored from the memberships (bookkeeper becomes owner), and installations
that hold several tenants lose the tenant boundary, not any bookkeeping rows.

The migration role must be a superuser or hold BYPASSRLS: the definer functions below and
pg_dump read tables that have forced policies.

Revision ID: 0004
Revises: 0003
Create Date: 2026-11-08
"""
import sqlalchemy as sa
from alembic import op

revision = "0004"
down_revision = "0003"
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


def upgrade() -> None:
    op.execute(
        """
        DO $$ BEGIN
            IF NOT (SELECT rolsuper OR rolbypassrls FROM pg_roles WHERE rolname = current_user) THEN
                RAISE EXCEPTION 'run migrations as a superuser or a BYPASSRLS role (see docs/DEPLOY.md)';
            END IF;
        END $$
        """
    )

    op.create_table(
        "tenants",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("name", sa.String(200), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"),
                  nullable=False),
    )
    op.execute(
        "INSERT INTO tenants (id, name) VALUES (1, "
        "coalesce((SELECT name FROM administrations ORDER BY id LIMIT 1), 'Default'))"
    )
    op.execute("SELECT setval(pg_get_serial_sequence('tenants', 'id'), 1)")

    op.create_table(
        "memberships",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="CASCADE"),
                  nullable=False),
        sa.Column("tenant_id", sa.Integer(), sa.ForeignKey("tenants.id", ondelete="CASCADE"),
                  nullable=False),
        sa.Column("role", sa.String(20), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"),
                  nullable=False),
        sa.UniqueConstraint("user_id", "tenant_id", name="uq_memberships_user_tenant"),
        sa.CheckConstraint("role IN ('owner', 'bookkeeper', 'accountant')",
                           name="ck_memberships_role"),
    )
    op.create_index("ix_memberships_tenant_id", "memberships", ["tenant_id"])
    op.execute(
        "INSERT INTO memberships (user_id, tenant_id, role) "
        "SELECT id, 1, CASE role WHEN 'owner' THEN 'owner' ELSE 'accountant' END FROM users"
    )
    op.drop_column("users", "role")

    # A constant default backfills existing rows without UPDATE (the ledger tables refuse it).
    for table in TENANT_TABLES:
        op.execute(f"ALTER TABLE {table} ADD COLUMN tenant_id integer NOT NULL DEFAULT 1")
        op.create_foreign_key(f"fk_{table}_tenant_id", table, "tenants", ["tenant_id"], ["id"])
        op.create_index(f"ix_{table}_tenant_id", table, ["tenant_id"])

    op.execute(
        """
        CREATE FUNCTION ledgerline_current_tenant() RETURNS integer
        LANGUAGE plpgsql STABLE AS $$
        DECLARE v text := nullif(current_setting('app.tenant_id', true), '');
        BEGIN
            IF v IS NULL THEN
                RAISE EXCEPTION 'no tenant selected (app.tenant_id is not set)'
                    USING ERRCODE = '42501';
            END IF;
            RETURN v::integer;
        END $$;
        """
    )
    for table in TENANT_TABLES:
        op.execute(f"ALTER TABLE {table} ALTER COLUMN tenant_id SET DEFAULT ledgerline_current_tenant()")
        op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
        op.execute(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY")
        op.execute(
            f"CREATE POLICY tenant_isolation ON {table} "
            "USING (tenant_id = ledgerline_current_tenant()) "
            "WITH CHECK (tenant_id = ledgerline_current_tenant())"
        )

    op.execute(
        f"""
        DO $$ BEGIN
            IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = '{APP_ROLE}') THEN
                CREATE ROLE {APP_ROLE} NOLOGIN;
            END IF;
        END $$;
        ALTER ROLE {APP_ROLE} NOSUPERUSER NOBYPASSRLS NOCREATEDB NOCREATEROLE;

        CREATE FUNCTION ledgerline_tenant_of_administration(admin_id integer) RETURNS integer
        LANGUAGE sql STABLE SECURITY DEFINER SET search_path = pg_catalog, public AS $$
            SELECT tenant_id FROM administrations WHERE id = admin_id
        $$;
        CREATE FUNCTION ledgerline_tenant_of_api_token(token_sha256 text) RETURNS integer
        LANGUAGE sql STABLE SECURITY DEFINER SET search_path = pg_catalog, public AS $$
            SELECT tenant_id FROM api_tokens WHERE api_tokens.token_sha256 = $1
        $$;
        REVOKE ALL ON FUNCTION ledgerline_tenant_of_administration(integer) FROM PUBLIC;
        REVOKE ALL ON FUNCTION ledgerline_tenant_of_api_token(text) FROM PUBLIC;

        GRANT USAGE ON SCHEMA public TO {APP_ROLE};
        GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA public TO {APP_ROLE};
        REVOKE ALL ON alembic_version FROM {APP_ROLE};
        GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO {APP_ROLE};
        GRANT EXECUTE ON ALL FUNCTIONS IN SCHEMA public TO {APP_ROLE};
        ALTER DEFAULT PRIVILEGES IN SCHEMA public
            GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO {APP_ROLE};
        ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT USAGE, SELECT ON SEQUENCES TO {APP_ROLE};
        ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT EXECUTE ON FUNCTIONS TO {APP_ROLE};
        """
    )


def downgrade() -> None:
    for table in TENANT_TABLES:
        op.execute(f"DROP POLICY tenant_isolation ON {table}")
        op.execute(f"ALTER TABLE {table} NO FORCE ROW LEVEL SECURITY")
        op.execute(f"ALTER TABLE {table} DISABLE ROW LEVEL SECURITY")
        op.execute(f"ALTER TABLE {table} ALTER COLUMN tenant_id DROP DEFAULT")

    op.execute(
        f"""
        ALTER DEFAULT PRIVILEGES IN SCHEMA public
            REVOKE SELECT, INSERT, UPDATE, DELETE ON TABLES FROM {APP_ROLE};
        ALTER DEFAULT PRIVILEGES IN SCHEMA public REVOKE USAGE, SELECT ON SEQUENCES FROM {APP_ROLE};
        ALTER DEFAULT PRIVILEGES IN SCHEMA public REVOKE EXECUTE ON FUNCTIONS FROM {APP_ROLE};
        DROP FUNCTION ledgerline_tenant_of_api_token(text);
        DROP FUNCTION ledgerline_tenant_of_administration(integer);
        DROP FUNCTION ledgerline_current_tenant();
        """
    )

    op.add_column("users", sa.Column("role", sa.String(20), nullable=False,
                                     server_default="accountant"))
    op.execute(
        "UPDATE users SET role = 'owner' WHERE EXISTS (SELECT 1 FROM memberships m "
        "WHERE m.user_id = users.id AND m.role IN ('owner', 'bookkeeper'))"
    )
    op.alter_column("users", "role", server_default=None)

    for table in TENANT_TABLES:
        op.drop_index(f"ix_{table}_tenant_id", table_name=table)
        op.drop_constraint(f"fk_{table}_tenant_id", table, type_="foreignkey")
        op.drop_column(table, "tenant_id")

    op.drop_table("memberships")
    op.drop_table("tenants")
    # The role is cluster-wide and may serve other databases, so it is kept; its privileges
    # on this database are revoked.
    op.execute(
        f"""
        REVOKE ALL ON ALL TABLES IN SCHEMA public FROM {APP_ROLE};
        REVOKE ALL ON ALL SEQUENCES IN SCHEMA public FROM {APP_ROLE};
        REVOKE ALL ON ALL FUNCTIONS IN SCHEMA public FROM {APP_ROLE};
        REVOKE USAGE ON SCHEMA public FROM {APP_ROLE};
        """
    )
