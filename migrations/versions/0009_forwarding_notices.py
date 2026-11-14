"""forwarding notices: confirmation links from mail forwarding setup

Tenant-scoped like the other books tables (row-level security, tenant_isolation policy), so
`ensure-app-role` grants it automatically. Several branches add migrations: re-chain
down_revision at merge time.

Revision ID: 0009
Revises: 0008
Create Date: 2026-11-14
"""
import sqlalchemy as sa
from alembic import op

revision = "0009"
down_revision = "0008"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "forwarding_notices",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("tenant_id", sa.Integer(), sa.ForeignKey("tenants.id"), nullable=False,
                  server_default=sa.text("ledgerline_current_tenant()")),
        sa.Column("administration_id", sa.Integer(), nullable=False),
        sa.Column("kind", sa.String(10), nullable=False),
        sa.Column("link", sa.String(600), nullable=False),
        sa.Column("received_at", sa.DateTime(timezone=True), server_default=sa.func.now(),
                  nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("dismissed_at", sa.DateTime(timezone=True)),
        sa.ForeignKeyConstraint(
            ["tenant_id", "administration_id"], ["administrations.tenant_id", "administrations.id"],
            name="forwarding_notices_tenant_id_administration_id_fkey"),
    )
    op.create_index("ix_forwarding_notices_tenant_id", "forwarding_notices", ["tenant_id"])
    op.create_index("ix_forwarding_notices_administration_id", "forwarding_notices",
                    ["administration_id"])
    op.execute("ALTER TABLE forwarding_notices ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE forwarding_notices FORCE ROW LEVEL SECURITY")
    op.execute(
        "CREATE POLICY tenant_isolation ON forwarding_notices "
        "USING (tenant_id = ledgerline_current_tenant()) "
        "WITH CHECK (tenant_id = ledgerline_current_tenant())"
    )
    op.execute(
        """
        DO $$ BEGIN
            IF EXISTS (SELECT FROM pg_roles WHERE rolname = 'ledgerline_app') THEN
                GRANT SELECT, INSERT, UPDATE, DELETE ON forwarding_notices TO ledgerline_app;
                GRANT USAGE, SELECT ON SEQUENCE forwarding_notices_id_seq TO ledgerline_app;
            END IF;
        END $$;
        """
    )


def downgrade() -> None:
    op.drop_table("forwarding_notices")
