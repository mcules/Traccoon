"""run_steps: the share newly written into the cache

Beside the share read from it. Kept because of a reading nobody can explain: on three turns
of one run the reported cache read was two and three times what the turns either side of it
reported, and the reported input was 4 and 6 instead of 2. It goes back to at least
2026-09-06, so it is not new. With the written share beside the read one the next occurrence
answers itself: both doubled means two requests were really made and paid for, only the read
one doubled means the accounting counts a cached stretch twice.

Revision ID: e7b3a05c1f48
Revises: d4a19c60b7f2
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "e7b3a05c1f48"
down_revision = "d4a19c60b7f2"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # `IF NOT EXISTS` because the column has two ways in: the schema healing the backend runs
    # at start put it there the moment the new code was loaded, and on this machine it did.
    # A migration that falls over the state its own application produced is a migration
    # nobody can run twice.
    op.execute("ALTER TABLE run_steps ADD COLUMN IF NOT EXISTS cache_write_tokens "
               "INTEGER DEFAULT 0 NOT NULL")


def downgrade() -> None:
    op.drop_column("run_steps", "cache_write_tokens")
