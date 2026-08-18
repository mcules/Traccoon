"""Timer-Knoten: warten, ohne dass jemand etwas melden muss

`wait_event` wartet auf ein Ereignis — jemand kommentiert, ein Mensch antwortet. Es gab
aber keinen Weg, schlicht Zeit vergehen zu lassen („in zwei Stunden nachsehen", „morgen
früh erinnern"), und ohne den ist jede Wiederholung nach einem Fehlschlag ein
Sofort-Wiederholen: dieselbe Gegenstelle, dieselbe Sekunde, derselbe Fehler.

Revision ID: d5a1c7f38e62
Revises: c3f8b1e29d47
Create Date: 2026-08-18
"""
from alembic import op

revision = 'd5a1c7f38e62'
down_revision = 'c3f8b1e29d47'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TYPE workflownodetype ADD VALUE IF NOT EXISTS 'timer'")


def downgrade() -> None:
    # Enum-Werte lassen sich in PostgreSQL nicht entfernen, ohne den Typ neu zu bauen.
    pass
