"""Who may write into a calendar

Being allowed to write and letting something write on your behalf are two
decisions, so the calendar carries three states rather than a flag. Next to it,
what the server itself said the last time it was asked — a permission the server
does not grant is one nobody should be offered.

Everything that already exists starts at `none`: a permission nobody has given
yet is a permission nobody has.

Revision ID: 9b1e4c7a25d3
Revises: 7adb362870be
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = '9b1e4c7a25d3'
down_revision = '7adb362870be'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("notes_calendars", sa.Column(
        "write_access", sa.String(length=16), nullable=False, server_default="none"))
    op.add_column("notes_calendars", sa.Column(
        "server_read_only", sa.Boolean(), nullable=False, server_default=sa.false()))


def downgrade() -> None:
    op.drop_column("notes_calendars", "server_read_only")
    op.drop_column("notes_calendars", "write_access")
