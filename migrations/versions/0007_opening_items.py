"""opening items: invoices carried over from a previous system

An opening item is a sales or purchase invoice that was already open on the start date. It is a
normal invoice row (so bank matching and aging see it) flagged `is_opening`; its journal entry
moves the open amount between debtors/creditors and an equity account, and never touches
revenue, expense or VAT accounts. No new table, so the tenancy rules are unchanged.

Revision ID: 0007
Revises: 0006
Create Date: 2026-11-10
"""
import sqlalchemy as sa
from alembic import op

revision = "0007"
down_revision = "0006"
branch_labels = None
depends_on = None

TABLES = ("sales_invoices", "purchase_invoices")


def upgrade() -> None:
    for table in TABLES:
        op.add_column(table, sa.Column("is_opening", sa.Boolean(), nullable=False,
                                       server_default=sa.false()))


def downgrade() -> None:
    for table in TABLES:
        op.drop_column(table, "is_opening")
