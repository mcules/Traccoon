"""How a mailbox behaves when things are thrown away

Two answers that belong to the mailbox, not to the code. Whether the trash marks what
lands in it as read is a question of who else reads this mailbox: alone, a discarded mail
is dealt with; on a shared one the marks say what the OTHERS have already seen.

And whether "mark everything read" asks first — the handle sits in the folder's own menu,
it is chosen deliberately, and its effect is undone message by message.

Revision ID: a1d7f3c95b20
Revises: f3c8a51d7e04
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = 'a1d7f3c95b20'
down_revision = 'f3c8a51d7e04'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("mail_accounts", sa.Column(
        "trash_marks_read", sa.Boolean(), nullable=False, server_default=sa.true()))
    op.add_column("mail_accounts", sa.Column(
        "ask_before_folder_read", sa.Boolean(), nullable=False, server_default=sa.false()))


def downgrade() -> None:
    op.drop_column("mail_accounts", "ask_before_folder_read")
    op.drop_column("mail_accounts", "trash_marks_read")
