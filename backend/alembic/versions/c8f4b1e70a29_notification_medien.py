"""Notification bekommt einen Medienausgang (media_path, media_kind)

Der Weg nach Telegram ist der Notifier im `telegram-bot`-Prozess, und zwar als einziger:
dem backend-Container fehlt `TELEGRAM_BOT_TOKEN` vollständig (er hat nur
`TELEGRAM_OWNER_CHAT`), und `bot.send_message` steht in `app/bot/__main__.py` an genau
einer Stelle. Damit das Backend trotzdem eine Datei mitschicken kann, trägt die
Notification den Pfad — der Bot liest ihn, wenn er die Zeile ohnehin abholt.

Beide Spalten sind nullable und ohne Vorgabe: für bestehende Zeilen ändert sich nichts,
der Notifier fällt bei leerem `media_path` auf den unveränderten Textweg zurück.

`media_kind` ist absichtlich VARCHAR und kein Enum (animation|photo|document): der Wert
entscheidet nur, welche aiogram-Methode gerufen wird, und ein Enum-Typ wäre in Postgres
ein Migrations-Hindernis für eine Liste, die sich mit Telegram ändert.

Der Live-Pfad ist `main.py::dev_create_all` (dort steht dasselbe DDL idempotent);
diese Revision ist der Pfad für `MIGRATE=1`.

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
