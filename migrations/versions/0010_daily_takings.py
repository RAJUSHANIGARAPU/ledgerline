"""daily takings: one row per till day, posted through the normal journal

Tenant table in the same shape as the others: tenant_id filled by the database, forced
row-level security, composite foreign keys that carry the tenant. The app role's grants come
from `ensure-app-role`, which reads the catalog.

Several branches add migrations; re-chain down_revision when merging.

Revision ID: 0010
Revises: 0009
Create Date: 2026-11-14
"""
import sqlalchemy as sa
from alembic import op

revision = "0010"
down_revision = "0009"
branch_labels = None
depends_on = None

TABLE = "daily_takings"
TENANT_FKS = (
    ("administration_id", "administrations"),
    ("journal_entry_id", "journal_entries"),
    ("reversal_entry_id", "journal_entries"),
)


def upgrade() -> None:
    op.create_table(
        TABLE,
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("tenant_id", sa.Integer(), sa.ForeignKey("tenants.id"), nullable=False,
                  server_default=sa.text("ledgerline_current_tenant()")),
        sa.Column("administration_id", sa.Integer(), nullable=False),
        sa.Column("takings_date", sa.Date(), nullable=False),
        sa.Column("gross_high_cents", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("gross_low_cents", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("gross_zero_cents", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("cash_cents", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("card_cents", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("other_cents", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("note", sa.String(200)),
        sa.Column("journal_entry_id", sa.Integer(), nullable=False),
        sa.Column("reversal_entry_id", sa.Integer()),
        sa.Column("reversed_at", sa.DateTime(timezone=True)),
        sa.Column("created_by_id", sa.Integer(), sa.ForeignKey("users.id")),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(),
                  nullable=False),
        sa.CheckConstraint(
            "gross_high_cents >= 0 AND gross_low_cents >= 0 AND gross_zero_cents >= 0 "
            "AND cash_cents >= 0 AND card_cents >= 0 AND other_cents >= 0",
            name="ck_takings_non_negative"),
        sa.CheckConstraint(
            "gross_high_cents + gross_low_cents + gross_zero_cents "
            "= cash_cents + card_cents + other_cents",
            name="ck_takings_balanced"),
        sa.UniqueConstraint("tenant_id", "id", name=f"uq_{TABLE}_tenant_id_id"),
    )
    op.create_index("ix_daily_takings_tenant_id", TABLE, ["tenant_id"])
    op.create_index("ix_daily_takings_administration_id", TABLE, ["administration_id"])
    op.create_index("ix_daily_takings_takings_date", TABLE, ["takings_date"])
    op.create_index("uq_daily_takings_active_day", TABLE, ["administration_id", "takings_date"],
                    unique=True, postgresql_where=sa.text("reversed_at IS NULL"))
    for column, target in TENANT_FKS:
        op.execute(
            f"ALTER TABLE {TABLE} ADD CONSTRAINT {TABLE}_{column}_fkey "
            f"FOREIGN KEY (tenant_id, {column}) REFERENCES {target} (tenant_id, id)"
        )
    op.execute(f"ALTER TABLE {TABLE} ENABLE ROW LEVEL SECURITY")
    op.execute(f"ALTER TABLE {TABLE} FORCE ROW LEVEL SECURITY")
    op.execute(
        f"CREATE POLICY tenant_isolation ON {TABLE} "
        "USING (tenant_id = ledgerline_current_tenant()) "
        "WITH CHECK (tenant_id = ledgerline_current_tenant())"
    )
    op.execute("GRANT SELECT, INSERT, UPDATE, DELETE ON daily_takings TO ledgerline_app")
    op.execute("GRANT USAGE, SELECT ON SEQUENCE daily_takings_id_seq TO ledgerline_app")


def downgrade() -> None:
    op.drop_table(TABLE)
