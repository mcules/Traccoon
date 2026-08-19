"""Notification channels belong to the person

Until now there was exactly one way: the bell and, if a chat id was stored, Telegram. Whoever
triggers a notification rarely knows whether the recipient uses Telegram at all. That is why
the person decides which way they are reached; the sender may prescribe a way but need not.

Revision ID: e7b2d4c19f38
Revises: d5a1c7f38e62
Create Date: 2026-08-18
"""
import sqlalchemy as sa
from alembic import op

revision = 'e7b2d4c19f38'
down_revision = 'd5a1c7f38e62'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("users", sa.Column("notify_default", sa.String(20),
                                     nullable=False, server_default="telegram"))
    op.add_column("users", sa.Column("notify_email", sa.String(255), nullable=True))
    # Whoever has no chat id was reached only over the bell anyway until now, and for them
    # e-mail is the more honest default, as far as an address is stored.
    op.execute("""
        UPDATE users SET notify_default = 'email'
         WHERE (telegram_chat_id IS NULL OR telegram_chat_id = '')
           AND email IS NOT NULL AND email <> ''
    """)


def downgrade() -> None:
    op.drop_column("users", "notify_email")
    op.drop_column("users", "notify_default")
