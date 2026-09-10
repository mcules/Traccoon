"""Where an appointment's line was last written

An index over the notes, not a second copy of them. Which appointment a line is
stays readable in the vault: the block id at the end of the line is computed from
the appointment's UID, so a note says what it is on a machine that has never seen
this table.

What the table buys is the question the files cannot answer quickly — an
appointment that moved out of a day nobody is currently looking at. Without a
note of where its line went, finding it would mean reading the whole vault, and
the day it left would go on claiming the appointment takes place there.

Throw the rows away and the next sync writes them again.

Revision ID: d7a41c9e0b53
Revises: c4e18b7f9a02
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = 'd7a41c9e0b53'
down_revision = 'c4e18b7f9a02'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "notes_calendar_marks",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("user_id", sa.Integer(),
                  sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("block_id", sa.String(length=40), nullable=False),
        sa.Column("note_path", sa.String(length=500), nullable=False),
        sa.Column("day", sa.Date(), nullable=False),
        sa.Column("uid", sa.String(length=500), nullable=False, server_default=""),
        sa.Column("seen_at", sa.DateTime(timezone=True),
                  server_default=sa.func.now(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True),
                  server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True),
                  server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint("user_id", "block_id", "note_path",
                            name="uq_notes_calendar_mark"),
    )
    op.create_index("ix_notes_calendar_marks_user_id", "notes_calendar_marks", ["user_id"])
    op.create_index("ix_notes_calendar_marks_block_id", "notes_calendar_marks", ["block_id"])
    op.create_index("ix_notes_calendar_marks_day", "notes_calendar_marks", ["day"])


def downgrade() -> None:
    op.drop_table("notes_calendar_marks")
