"""account security: invitations, password reset, two-factor login

Global or tenant-scoped, decided per table (docs/adr/0001-multi-tenancy.md):

- auth_tokens: GLOBAL. Looked up from an emailed link before anyone is signed in, so no tenant
  is known yet; a policy on tenant_id would need another definer function for no gain. It has no
  tenant_id column on purpose: an invite points at a pending row in memberships, which carries
  the tenant, so a token can only ever activate that one membership.
- user_totp, recovery_codes: GLOBAL. Two-factor is a property of the person, valid in every
  tenant they belong to. The secret is Fernet-encrypted; recovery codes are keyed hashes.
- tenants.require_2fa: TENANT-LEVEL setting on the (global) tenants row, set by the owner.
- memberships.status: pending invitations grant no access until accepted.
- users.session_version: sessions carry it, so a password change or reset ends other sessions.

New global tables are granted to the app role explicitly (no default privileges exist) and added
to SHARED_TABLES in cli.py so `ensure-app-role` keeps granting them.

Revision ID: 0006
Revises: 0005
Create Date: 2026-11-08
"""
import sqlalchemy as sa
from alembic import op

revision = "0006"
down_revision = "0005"
branch_labels = None
depends_on = None

NEW_TABLES = ("auth_tokens", "user_totp", "recovery_codes")


def upgrade() -> None:
    op.add_column("users", sa.Column("session_version", sa.Integer(), nullable=False,
                                     server_default="1"))
    op.add_column("tenants", sa.Column("require_2fa", sa.Boolean(), nullable=False,
                                       server_default=sa.false()))
    op.add_column("memberships", sa.Column("status", sa.String(10), nullable=False,
                                           server_default="active"))
    op.create_check_constraint("ck_memberships_status", "memberships",
                               "status IN ('active', 'pending')")

    op.create_table(
        "auth_tokens",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="CASCADE"),
                  nullable=False),
        sa.Column("membership_id", sa.Integer(),
                  sa.ForeignKey("memberships.id", ondelete="CASCADE")),
        sa.Column("purpose", sa.String(10), nullable=False),
        sa.Column("token_hash", sa.String(64), nullable=False, unique=True),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("used_at", sa.DateTime(timezone=True)),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(),
                  nullable=False),
        sa.CheckConstraint("purpose IN ('invite', 'reset')", name="ck_auth_tokens_purpose"),
    )
    op.create_index("ix_auth_tokens_user_id", "auth_tokens", ["user_id"])

    op.create_table(
        "user_totp",
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="CASCADE"),
                  primary_key=True),
        sa.Column("secret_enc", sa.Text(), nullable=False),
        sa.Column("enabled_at", sa.DateTime(timezone=True)),
        sa.Column("last_used_step", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(),
                  nullable=False),
    )
    op.create_table(
        "recovery_codes",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="CASCADE"),
                  nullable=False),
        sa.Column("code_hash", sa.String(64), nullable=False),
        sa.Column("used_at", sa.DateTime(timezone=True)),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(),
                  nullable=False),
        sa.UniqueConstraint("user_id", "code_hash", name="uq_recovery_codes_user_hash"),
    )
    op.create_index("ix_recovery_codes_user_id", "recovery_codes", ["user_id"])

    op.execute(
        """
        DO $$ BEGIN
            IF EXISTS (SELECT FROM pg_roles WHERE rolname = 'ledgerline_app') THEN
                GRANT SELECT, INSERT, UPDATE, DELETE ON auth_tokens, user_totp, recovery_codes
                    TO ledgerline_app;
                GRANT USAGE, SELECT ON SEQUENCE auth_tokens_id_seq, recovery_codes_id_seq
                    TO ledgerline_app;
            END IF;
        END $$;
        """
    )


def downgrade() -> None:
    for table in reversed(NEW_TABLES):
        op.drop_table(table)
    op.drop_constraint("ck_memberships_status", "memberships")
    op.drop_column("memberships", "status")
    op.drop_column("tenants", "require_2fa")
    op.drop_column("users", "session_version")
