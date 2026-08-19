"""Timer node: waiting without anybody having to report anything

`wait_event` waits for an event: somebody comments, a human answers. But there was no way to
let time simply pass ("look again in two hours", "", „morgen
remind me tomorrow morning"), and without one every retry after a failure is an immediate
retry: the same counterpart, the same second, the same error.

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
    # Enum values cannot be removed in PostgreSQL without rebuilding the type.
    pass
