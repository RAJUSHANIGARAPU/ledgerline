"""one reversal per journal entry; left-out bank lines kept server-side

- journal_entries: a partial unique index on reversal_of_id, so two concurrent requests cannot
  both reverse the same entry (the application check alone is check-then-act). The create fails
  if an entry was already reversed twice; resolve those rows first.
- bank_statements.left_out: the lines of an import that were left out as duplicates, so the
  override names a line by key instead of submitting its amount, date and IBAN.
  No new table, so the tenancy rules are unchanged.

Revision ID: 0013
Revises: 0012
Create Date: 2026-11-14
"""
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0013"
down_revision = "0012"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_index(
        "uq_journal_entries_reversal_of", "journal_entries", ["reversal_of_id"], unique=True,
        postgresql_where=sa.text("reversal_of_id IS NOT NULL"),
    )
    op.add_column("bank_statements", sa.Column("left_out", postgresql.JSONB(), nullable=True))


def downgrade() -> None:
    op.drop_column("bank_statements", "left_out")
    op.drop_index("uq_journal_entries_reversal_of", table_name="journal_entries")
