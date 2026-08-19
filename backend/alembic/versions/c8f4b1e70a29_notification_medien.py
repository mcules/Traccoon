"""Notification gets a media output (media_path, media_kind)

The way to Telegram is the notifier in the `telegram-bot` process, and it is the only one:
the backend container lacks `TELEGRAM_BOT_TOKEN` entirely (it only has
`TELEGRAM_OWNER_CHAT`), and `bot.send_message` stands in `app/bot/__main__.py` in exactly one
place. So that the backend can send a file along regardless, the notification carries the
path, and the bot reads it when it fetches the row anyway.

Both columns are nullable and without a default: nothing changes for existing rows, and with
an empty `media_path` the notifier falls back on the unchanged text path.

`media_kind` is deliberately a VARCHAR and not an enum (animation|photo|document): the value
only decides which aiogram method is called, and an enum type would be an obstacle for
migrations of a list that changes with Telegram.

The live path is `main.py::dev_create_all` (the same DDL stands there idempotently); this
revision is the path for `MIGRATE=1`.

Revision ID: c8f4b1e70a29
Revises: b2e7f9c14a08
Create Date: 2026-08-05
"""
from alembic import op
import sqlalchemy as sa


revision = 'c8f4b1e70a29'
down_revision = 'b2e7f9c14a08'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column('notifications', sa.Column('media_path', sa.String(length=500), nullable=True))
    op.add_column('notifications', sa.Column('media_kind', sa.String(length=20), nullable=True))


def downgrade() -> None:
    op.drop_column('notifications', 'media_kind')
    op.drop_column('notifications', 'media_path')
