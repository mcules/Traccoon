"""Drossel: höchstens alle N Minuten dieselbe Nachricht

Traccar dedupliziert Alarme ausdrücklich nicht: solange ein Alarmbit gesetzt ist, entsteht
ein Ereignis je eingehender Position — im Wachbetrieb alle paar Sekunden. Ohne Drossel wird
aus zehn Minuten Erschütterung ein Strom von rund 120 gleichlautenden Nachrichten. Der
Schlüssel entscheidet, was als „dieselbe Nachricht" gilt; er kommt aus dem Ablauf.

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
