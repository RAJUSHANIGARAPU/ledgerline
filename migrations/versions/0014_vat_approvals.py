"""VAT return approvals: who confirmed which figures for a period

Tenant table in the same shape as the others: tenant_id filled by the database, forced
row-level security, composite foreign key that carries the tenant. One row per administration
and period; approving again replaces the figures (earlier approvals stay in audit_events,
which is append-only). The app role's grants come from `ensure-app-role`, which reads the
catalog; the grants below cover databases where it already ran.

Revision ID: 0014
Revises: 0013
Create Date: 2026-11-15
"""
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0014"
down_revision = "0013"
branch_labels = None
depends_on = None

TABLE = "vat_approvals"


def upgrade() -> None:
    op.create_table(
        TABLE,
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("tenant_id", sa.Integer(), sa.ForeignKey("tenants.id"), nullable=False,
                  server_default=sa.text("ledgerline_current_tenant()")),
        sa.Column("administration_id", sa.Integer(), nullable=False),
        sa.Column("period_start", sa.Date(), nullable=False),
        sa.Column("period_end", sa.Date(), nullable=False),
        sa.Column("figures", postgresql.JSONB(), nullable=False),
        sa.Column("approved_by_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("approved_at", sa.DateTime(timezone=True), server_default=sa.func.now(),
                  nullable=False),
        sa.UniqueConstraint("administration_id", "period_start", "period_end",
                            name="uq_vat_approvals_period"),
        sa.UniqueConstraint("tenant_id", "id", name=f"uq_{TABLE}_tenant_id_id"),
    )
    op.create_index("ix_vat_approvals_tenant_id", TABLE, ["tenant_id"])
    op.create_index("ix_vat_approvals_administration_id", TABLE, ["administration_id"])
    op.execute(
        f"ALTER TABLE {TABLE} ADD CONSTRAINT {TABLE}_administration_id_fkey "
        "FOREIGN KEY (tenant_id, administration_id) REFERENCES administrations (tenant_id, id)"
    )
    op.execute(f"ALTER TABLE {TABLE} ENABLE ROW LEVEL SECURITY")
    op.execute(f"ALTER TABLE {TABLE} FORCE ROW LEVEL SECURITY")
    op.execute(
        f"CREATE POLICY tenant_isolation ON {TABLE} "
        "USING (tenant_id = ledgerline_current_tenant()) "
        "WITH CHECK (tenant_id = ledgerline_current_tenant())"
    )
    op.execute(f"GRANT SELECT, INSERT, UPDATE, DELETE ON {TABLE} TO ledgerline_app")
    op.execute(f"GRANT USAGE, SELECT ON SEQUENCE {TABLE}_id_seq TO ledgerline_app")


def downgrade() -> None:
    op.drop_table(TABLE)
