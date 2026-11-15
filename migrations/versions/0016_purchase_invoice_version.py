"""version column on purchase invoices, for optimistic locking of draft edits

The edit form carries the version it was loaded with; a save with another version is refused
and a successful save increments it. A constant server default, so no rows are rewritten.

Revision ID: 0016
Revises: 0015
Create Date: 2026-11-15
"""
import sqlalchemy as sa
from alembic import op

revision = "0016"
down_revision = "0015"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("purchase_invoices", sa.Column("version", sa.Integer(), nullable=False,
                                                 server_default="1"))


def downgrade() -> None:
    op.drop_column("purchase_invoices", "version")
