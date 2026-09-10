"""assistant_sessions: what this conversation runs on

Three overrides, all empty by default, and empty means "whatever the agent is set to" rather
than any value of their own. Per conversation because the reason to reach for a deeper level
or a faster model is the subject at hand.

Revision ID: b8d34f1a7c05
Revises: f1c62d80a934
"""
from __future__ import annotations

from alembic import op

revision = "b8d34f1a7c05"
down_revision = "f1c62d80a934"
branch_labels = None
depends_on = None

_COLUMNS = (
    "model VARCHAR(150) DEFAULT '' NOT NULL",
    "effort VARCHAR(10) DEFAULT '' NOT NULL",
    "fast BOOLEAN DEFAULT FALSE NOT NULL",
)


def upgrade() -> None:
    # `IF NOT EXISTS`: the schema healing the backend runs at start puts the columns there
    # the moment the new code is loaded, so the migration has to survive its own application.
    for col in _COLUMNS:
        op.execute(f"ALTER TABLE assistant_sessions ADD COLUMN IF NOT EXISTS {col}")


def downgrade() -> None:
    for col in _COLUMNS:
        op.drop_column("assistant_sessions", col.split()[0])
