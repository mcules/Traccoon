"""UserStatus.placeholder — Platzhalter-Konten für Personen-Zuweisung ohne Login

Revision ID: b4e6f1a92c83
Revises: a3b1c9d72f40
Create Date: 2026-07-22
"""
import sqlalchemy as sa
from alembic import op

revision = 'b4e6f1a92c83'
down_revision = 'a3b1c9d72f40'
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Neuer Enum-Wert für Konten, die nur als Zuweisungsziel existieren (kein Login).
    op.execute("ALTER TYPE userstatus ADD VALUE IF NOT EXISTS 'placeholder'")


def downgrade() -> None:
    # Postgres kann Enum-Werte nicht entfernen — Downgrade ist ein No-Op (Doku-Zweck).
    pass
