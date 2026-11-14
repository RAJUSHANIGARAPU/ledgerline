"""pilot feedback entries

Tenant-scoped like every table that holds a customer's data (docs/adr/0001-multi-tenancy.md):
forced row-level security, composite foreign key to the administration, explicit grant to the
application role. The text is personal data; tenant offboarding deletes it with the tenant.

Revision ID: 0007
Revises: 0006
Create Date: 2026-11-14
"""
import sqlalchemy as sa
from alembic import op

revision = "0007"
down_revision = "0006"
branch_labels = None
depends_on = None

APP_ROLE = "ledgerline_app"


def upgrade() -> None:
    op.create_table(
        "feedback_entries",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("tenant_id", sa.Integer(), sa.ForeignKey("tenants.id"), nullable=False,
                  server_default=sa.text("ledgerline_current_tenant()")),
        sa.Column("administration_id", sa.Integer(), nullable=False),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("body", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"),
                  nullable=False),
        sa.ForeignKeyConstraint(["tenant_id", "administration_id"],
                                ["administrations.tenant_id", "administrations.id"]),
        sa.CheckConstraint("length(btrim(body)) > 0", name="ck_feedback_entries_body"),
    )
    op.create_index("ix_feedback_entries_tenant_id", "feedback_entries", ["tenant_id"])
    op.create_index("ix_feedback_entries_administration_id", "feedback_entries",
                    ["administration_id"])
    op.execute("ALTER TABLE feedback_entries ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE feedback_entries FORCE ROW LEVEL SECURITY")
    op.execute(
        "CREATE POLICY tenant_isolation ON feedback_entries "
        "USING (tenant_id = ledgerline_current_tenant()) "
        "WITH CHECK (tenant_id = ledgerline_current_tenant())"
    )
    op.execute(
        f"""
        DO $$ BEGIN
            IF EXISTS (SELECT FROM pg_roles WHERE rolname = '{APP_ROLE}') THEN
                GRANT SELECT, INSERT, UPDATE, DELETE ON feedback_entries TO {APP_ROLE};
                GRANT USAGE, SELECT ON SEQUENCE feedback_entries_id_seq TO {APP_ROLE};
            END IF;
        END $$;
        """
    )


def downgrade() -> None:
    op.drop_table("feedback_entries")
