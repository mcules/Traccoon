"""What kind of appointment wrote a line

Asked only in the case where it can no longer be asked: the appointment has
vanished from the calendar, and what happens to its line depends on what it was.
A series that ended is cleared out of the future — leaving a struck-through
phantom in every note of the next year would be noise, not history. A single
appointment that was dropped stays, struck through: it had been planned, and
that is worth keeping.

Revision ID: e2b7f04a1c69
Revises: d7a41c9e0b53
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = 'e2b7f04a1c69'
down_revision = 'd7a41c9e0b53'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("notes_calendar_marks",
                  sa.Column("series", sa.String(length=16), nullable=False,
                            server_default=""))


def downgrade() -> None:
    op.drop_column("notes_calendar_marks", "series")
