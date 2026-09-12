"""assistant_files: what a person hands the assistant along with a chat message

Revision ID: a7c4e2f91b30
Revises: d4b1f0e29a77
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "a7c4e2f91b30"
down_revision = "d4b1f0e29a77"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # create_all at backend start puts the table there the moment the new code is loaded,
    # so the migration has to survive its own application: nothing to do when it is there.
    if sa.inspect(op.get_bind()).has_table("assistant_files"):
        return
    op.create_table(
        "assistant_files",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("owner_user_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="CASCADE"),
                  nullable=True, index=True),
        sa.Column("task_id", sa.Integer(), sa.ForeignKey("assistant_tasks.id", ondelete="SET NULL"),
                  nullable=True, index=True),
        sa.Column("filename", sa.String(500), nullable=False),
        sa.Column("mime_type", sa.String(120), nullable=False, server_default="application/octet-stream"),
        sa.Column("size", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("data", sa.LargeBinary(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )


def downgrade() -> None:
    op.drop_table("assistant_files")
