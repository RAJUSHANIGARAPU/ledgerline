"""bank CSV import and manual bank lines

- bank_accounts.csv_mapping: the column mapping last used for a generic CSV export, so the next
  file of that account needs no mapping step. No new table, so row-level security on
  bank_accounts already covers it.
- bank_transactions.entry_source / entered_by_user_id: a line typed in by a person is marked
  'manual' and has no statement file behind it (statement_id is therefore nullable, tied to the
  source by a check constraint).

Revision ID: 0011
Revises: 0010
Create Date: 2026-11-14

Another branch may also add a revision on top of 0006. Whoever merges second re-chains this one:
set down_revision to the other head and run `alembic heads` to confirm a single head.
"""
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0011"
down_revision = "0010"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("bank_accounts", sa.Column("csv_mapping", postgresql.JSONB(), nullable=True))
    op.add_column("bank_transactions", sa.Column(
        "entry_source", sa.String(10), nullable=False, server_default="import"))
    op.add_column("bank_transactions", sa.Column(
        "entered_by_user_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=True))
    op.alter_column("bank_transactions", "statement_id", nullable=True)
    op.create_check_constraint("ck_bank_tx_entry_source", "bank_transactions",
                               "entry_source IN ('import', 'manual')")
    op.create_check_constraint("ck_bank_tx_manual_no_file", "bank_transactions",
                               "(entry_source = 'manual') = (statement_id IS NULL)")


def downgrade() -> None:
    op.drop_constraint("ck_bank_tx_manual_no_file", "bank_transactions", type_="check")
    op.drop_constraint("ck_bank_tx_entry_source", "bank_transactions", type_="check")
    manual = op.get_bind().execute(
        sa.text("SELECT count(*) FROM bank_transactions WHERE statement_id IS NULL")).scalar()
    if manual:
        raise RuntimeError(f"{manual} manually entered bank lines exist; they have no statement "
                           "file and would be lost. Remove them first.")
    op.alter_column("bank_transactions", "statement_id", nullable=False)
    op.drop_column("bank_transactions", "entered_by_user_id")
    op.drop_column("bank_transactions", "entry_source")
    op.drop_column("bank_accounts", "csv_mapping")
