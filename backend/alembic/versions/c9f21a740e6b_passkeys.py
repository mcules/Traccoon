"""Passkeys: the public half of a key somebody logs in with

Only the public half is here, and that is the point of the whole thing: this table leaking
gives nobody a way in, unlike a table of password hashes.

Revision ID: c9f21a740e6b
Revises: b2e8c410af73
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = 'c9f21a740e6b'
down_revision = 'b2e8c410af73'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "passkeys",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("user_id", sa.Integer(),
                  sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("credential_id", sa.String(length=400), nullable=False, unique=True),
        sa.Column("public_key", sa.LargeBinary(), nullable=False),
        sa.Column("sign_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("label", sa.String(length=80), nullable=False, server_default=""),
        sa.Column("kind", sa.String(length=20), nullable=False, server_default=""),
        sa.Column("last_used_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True),
                  server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True),
                  server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_passkeys_user_id", "passkeys", ["user_id"])
    op.create_index("ix_passkeys_credential_id", "passkeys", ["credential_id"])


def downgrade() -> None:
    op.drop_table("passkeys")
