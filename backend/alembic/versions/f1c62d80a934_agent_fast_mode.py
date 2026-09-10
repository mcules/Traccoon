"""agent_definitions: fast mode per agent

The same model, writing up to two and a half times as fast, at twice the price per token.
Off by default and worth switching on exactly where somebody is waiting in front of the
answer: the wall clock of an agent run IS its output divided by the writing speed.

Revision ID: f1c62d80a934
Revises: e7b3a05c1f48
"""
from __future__ import annotations

from alembic import op

revision = "f1c62d80a934"
down_revision = "e7b3a05c1f48"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # `IF NOT EXISTS`: the schema healing the backend runs at start puts the column there the
    # moment the new code is loaded, so the migration has to survive its own application.
    op.execute("ALTER TABLE agent_definitions ADD COLUMN IF NOT EXISTS fast BOOLEAN "
               "DEFAULT FALSE NOT NULL")


def downgrade() -> None:
    op.drop_column("agent_definitions", "fast")
