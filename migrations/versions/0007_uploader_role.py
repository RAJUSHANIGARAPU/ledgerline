"""uploader role: capture-only membership

Adds 'uploader' to ck_memberships_role. No data changes.

Chained on 0006 because main is at 0006. Other unmerged branches also add a 0007: whichever
lands later must change down_revision below to the head that landed first.

Revision ID: 0007_uploader_role
Revises: 0006
Create Date: 2026-11-14
"""
from alembic import op

revision = "0007_uploader_role"
down_revision = "0006"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.drop_constraint("ck_memberships_role", "memberships", type_="check")
    op.create_check_constraint(
        "ck_memberships_role", "memberships",
        "role IN ('owner', 'bookkeeper', 'accountant', 'uploader')")


def downgrade() -> None:
    op.execute("UPDATE memberships SET role = 'accountant' WHERE role = 'uploader'")
    op.drop_constraint("ck_memberships_role", "memberships", type_="check")
    op.create_check_constraint(
        "ck_memberships_role", "memberships", "role IN ('owner', 'bookkeeper', 'accountant')")
