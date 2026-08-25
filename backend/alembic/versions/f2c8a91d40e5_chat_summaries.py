"""Conversation memory: a continuously written summary per conversation thread

The history of the assistant was a time window: the last eight exchanges within twelve hours,
everything before that gone without replacement. The reference was therefore not lost
gradually but abruptly: the human referred to yesterday, the assistant knew only the last
hour and seemed clueless.

Older exchanges now wander into a summary that grows along (the model being the context
compaction). One row per (human, agent): it is written on, not multiplied.

Revision ID: f2c8a91d40e5
Revises: e3f9c07a2b16
Create Date: 2026-07-29
"""
from alembic import op
import sqlalchemy as sa


revision = 'f2c8a91d40e5'
down_revision = 'e3f9c07a2b16'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "chat_summaries",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("owner_user_id", sa.Integer(),
                  sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=True),
        sa.Column("agent", sa.String(length=100), nullable=False, server_default="assistent"),
        sa.Column("bis_task_id", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("text", sa.Text(), nullable=False, server_default=""),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.func.now()),
    )
    op.create_index("ix_chat_summaries_owner_user_id", "chat_summaries", ["owner_user_id"])
    op.create_unique_constraint("uq_chat_summary_faden", "chat_summaries",
                                ["owner_user_id", "agent"])


def downgrade() -> None:
    op.drop_constraint("uq_chat_summary_faden", "chat_summaries", type_="unique")
    op.drop_index("ix_chat_summaries_owner_user_id", table_name="chat_summaries")
    op.drop_table("chat_summaries")
