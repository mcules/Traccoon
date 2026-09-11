"""issues.closed_reason: why a ticket was closed unfinished

Revision ID: c2e9a7d41b63
Revises: b8d34f1a7c05
"""
from __future__ import annotations

from alembic import op

revision = "c2e9a7d41b63"
down_revision = "b8d34f1a7c05"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # `IF NOT EXISTS`: the schema healing at backend start puts the column there the moment
    # the new code is loaded, so the migration has to survive its own application.
    op.execute("ALTER TABLE issues ADD COLUMN IF NOT EXISTS closed_reason VARCHAR(30)")


def downgrade() -> None:
    op.drop_column("issues", "closed_reason")
