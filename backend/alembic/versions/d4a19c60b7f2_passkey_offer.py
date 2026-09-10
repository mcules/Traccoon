"""When somebody said they did not want a passkey

Empty means: not asked yet, and then the offer stands once. A moment and not a flag, so the
answer can be read later — "asked in March and said no" is a different state from "never
heard of it", and only one of the two is worth bringing up again after a year.

Revision ID: d4a19c60b7f2
Revises: c9f21a740e6b
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = 'd4a19c60b7f2'
down_revision = 'c9f21a740e6b'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("users", sa.Column("passkey_declined_at",
                                     sa.DateTime(timezone=True), nullable=True))


def downgrade() -> None:
    op.drop_column("users", "passkey_declined_at")
