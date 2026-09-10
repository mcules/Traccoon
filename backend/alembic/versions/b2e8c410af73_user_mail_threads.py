"""Whether the message list groups conversations

On the person and not in the browser: it is how somebody reads mail, and reading mail at
the desk and on the phone is the same habit. Off by default — an existing list must not
rearrange itself on an update.

Revision ID: b2e8c410af73
Revises: a1d7f3c95b20
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = 'b2e8c410af73'
down_revision = 'a1d7f3c95b20'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("users", sa.Column(
        "mail_threads", sa.Boolean(), nullable=False, server_default=sa.false()))


def downgrade() -> None:
    op.drop_column("users", "mail_threads")
