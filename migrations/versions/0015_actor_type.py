"""actor_type on audit events and journal entries

Who a write came from when no user is behind it: 'user', 'worker', 'system' (CLI) or
'unknown'. Rows written before this migration are 'unknown'.

The columns are added with a constant server default instead of an UPDATE: journal_entries
refuses UPDATE through a trigger, and a constant default needs no table rewrite or row update
(PostgreSQL 11+). The default stays, so a writer that forgets the column is recorded as
'unknown' rather than failing.

Revision ID: 0015
Revises: 0014
Create Date: 2026-11-15
"""
import sqlalchemy as sa
from alembic import op

revision = "0015"
down_revision = "0014"
branch_labels = None
depends_on = None

TABLES = ("audit_events", "journal_entries")


def upgrade() -> None:
    for table in TABLES:
        op.add_column(table, sa.Column("actor_type", sa.String(10), nullable=False,
                                       server_default="unknown"))
        op.create_check_constraint(
            f"ck_{table}_actor_type", table,
            "actor_type IN ('user', 'worker', 'system', 'unknown')")


def downgrade() -> None:
    for table in TABLES:
        op.drop_constraint(f"ck_{table}_actor_type", table, type_="check")
        op.drop_column(table, "actor_type")
