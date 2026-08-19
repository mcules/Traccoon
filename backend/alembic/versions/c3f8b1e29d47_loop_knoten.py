"""Loop node: walking through a list element by element

Until now a flow executed every step exactly once. "For every row", "for every mail", "for
every document" could not be built with that: one got up to the data but not through it.


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
    # PG allows ADD VALUE in a transaction as long as the value is not used inside it.
    op.execute("ALTER TYPE workflownodetype ADD VALUE IF NOT EXISTS 'loop'")


def downgrade() -> None:
    # Enum values cannot be removed in PostgreSQL without rebuilding the type, and a rolled
    # back value would make existing step rows unreadable.
    pass
