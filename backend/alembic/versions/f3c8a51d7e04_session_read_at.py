"""How far a conversation has been read

The one state a conversation has that it knows nothing about by itself. Compared
against `last_message_at` it says whether an answer is still waiting to be seen,
which is what turns a number in the switcher green.

On the session and not in a browser: an answer read at the desk is read on the
phone too, and a session already belongs to exactly one person.

Revision ID: f3c8a51d7e04
Revises: e2b7f04a1c69
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = 'f3c8a51d7e04'
down_revision = 'e2b7f04a1c69'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("assistant_sessions",
                  sa.Column("read_at", sa.DateTime(timezone=True), nullable=True))


def downgrade() -> None:
    op.drop_column("assistant_sessions", "read_at")
