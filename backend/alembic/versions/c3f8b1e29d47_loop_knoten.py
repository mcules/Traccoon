"""Schleifen-Knoten: eine Liste Element für Element durchgehen

Bis hierhin führte ein Ablauf jeden Schritt genau einmal aus. „Für jede Zeile", „für jede
Mail", „für jedes Dokument" ließ sich damit nicht bauen — man kam an die Daten heran, aber
nicht durch sie hindurch.

Revision ID: c3f8b1e29d47
Revises: b7e1d3a94c52
Create Date: 2026-08-18
"""
from alembic import op

revision = 'c3f8b1e29d47'
down_revision = 'b7e1d3a94c52'
branch_labels = None
depends_on = None


def upgrade() -> None:
    # PG erlaubt ADD VALUE in einer Transaktion, solange der Wert darin nicht benutzt wird.
    op.execute("ALTER TYPE workflownodetype ADD VALUE IF NOT EXISTS 'loop'")


def downgrade() -> None:
    # Enum-Werte lassen sich in PostgreSQL nicht entfernen, ohne den Typ neu zu bauen — und
    # ein zurückgerollter Wert würde bestehende Schritt-Zeilen unlesbar machen.
    pass
