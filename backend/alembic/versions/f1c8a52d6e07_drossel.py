"""Throttle: the same message at most every N minutes

Traccar explicitly does not deduplicate alarms: as long as an alarm bit is set, an event
arises per incoming position, every few seconds in guard mode. Without a throttle, ten
minutes of shaking become a stream of around 120 identical messages. The key decides what
counts as "the same message"; it comes from the flow.

Revision ID: f1c8a52d6e07
Revises: e7b2d4c19f38
Create Date: 2026-08-18
"""
import sqlalchemy as sa
from alembic import op

revision = 'f1c8a52d6e07'
down_revision = 'e7b2d4c19f38'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("notifications", sa.Column("drossel_key", sa.String(160), nullable=True))
    op.create_index("ix_notifications_drossel", "notifications", ["drossel_key", "created_at"])


def downgrade() -> None:
    op.drop_index("ix_notifications_drossel", table_name="notifications")
    op.drop_column("notifications", "drossel_key")
