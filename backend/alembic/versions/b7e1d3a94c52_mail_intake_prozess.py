"""Mail inbox as a process: the spam verdict knows its flow

The way of an incoming mail (classify, assess, ask, move) runs as a graph from now on (slot
`mail_intake`, trigger `mail.received`). So that the answer from Telegram advances the flow
instead of moving the mail past it, the question carries its instance.


Old verdicts keep NULL: for them the direct way remains (see `spam_review.entscheiden`).

Revision ID: b7e1d3a94c52
Revises: d4a7c2b91f63
Create Date: 2026-08-18
"""
from alembic import op
import sqlalchemy as sa


revision = 'b7e1d3a94c52'
down_revision = 'd4a7c2b91f63'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("spam_verdicts",
                  sa.Column("workflow_instance_id", sa.Integer(), nullable=True))
    op.create_foreign_key("fk_spam_verdicts_workflow_instance", "spam_verdicts",
                          "workflow_instances", ["workflow_instance_id"], ["id"],
                          ondelete="SET NULL")
    op.create_index("ix_spam_verdicts_workflow_instance_id", "spam_verdicts",
                    ["workflow_instance_id"])


def downgrade() -> None:
    op.drop_index("ix_spam_verdicts_workflow_instance_id", table_name="spam_verdicts")
    op.drop_constraint("fk_spam_verdicts_workflow_instance", "spam_verdicts",
                       type_="foreignkey")
    op.drop_column("spam_verdicts", "workflow_instance_id")
