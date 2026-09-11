"""notifications: the Telegram message id and whether its buttons still stand

Revision ID: d4b1f0e29a77
Revises: c2e9a7d41b63
"""
from __future__ import annotations

from alembic import op

revision = "d4b1f0e29a77"
down_revision = "c2e9a7d41b63"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # `IF NOT EXISTS`: the schema healing at backend start puts the columns there the moment
    # the new code is loaded, so the migration has to survive its own application.
    op.execute("ALTER TABLE notifications ADD COLUMN IF NOT EXISTS tg_message_id INTEGER")
    op.execute("ALTER TABLE notifications ADD COLUMN IF NOT EXISTS buttons_open BOOLEAN "
               "DEFAULT FALSE NOT NULL")


def downgrade() -> None:
    op.drop_column("notifications", "buttons_open")
    op.drop_column("notifications", "tg_message_id")
