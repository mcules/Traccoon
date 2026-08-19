"""Spam detection: contact mirror, verdicts and learned features

The mail triage could classify until now but clear nothing away. Three tables are added:

* `assistant_contacts`: known addresses from the Obsidian vault (the acquittal list);
* `spam_verdicts`: one verdict per assessed mail including the decision of the human;
* `spam_feature_stats`: the counters learned from that, which enter every future assessment.
  Without them the detection would stay equally clever forever and the human would answer the
  same question about the same sender endlessly.

`notifications.spam_verdict_id` gives the Telegram card its reference (with the digest card
it points at the first case of the collection).

Revision ID: d4a7c2b91f63
Revises: c8f4b1e70a29
Create Date: 2026-08-17
"""
from alembic import op
import sqlalchemy as sa


revision = 'd4a7c2b91f63'
down_revision = 'c8f4b1e70a29'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "assistant_contacts",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("owner_user_id", sa.Integer(),
                  sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=True),
        sa.Column("email", sa.String(length=320), nullable=False, server_default=""),
        sa.Column("domain", sa.String(length=255), nullable=False, server_default=""),
        sa.Column("name", sa.String(length=300), nullable=False, server_default=""),
        sa.Column("source_path", sa.String(length=500), nullable=False, server_default=""),
        sa.Column("source_kind", sa.String(length=20), nullable=False,
                  server_default="frontmatter"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.func.now()),
    )
    op.create_index("ix_assistant_contacts_owner_user_id", "assistant_contacts",
                    ["owner_user_id"])
    op.create_index("ix_assistant_contacts_email", "assistant_contacts", ["email"])
    op.create_index("ix_assistant_contacts_domain", "assistant_contacts", ["domain"])
    op.create_unique_constraint("uq_assistant_contact", "assistant_contacts",
                                ["owner_user_id", "email"])

    op.create_table(
        "spam_verdicts",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("owner_user_id", sa.Integer(),
                  sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=True),
        sa.Column("assistant_task_id", sa.Integer(),
                  sa.ForeignKey("assistant_tasks.id", ondelete="CASCADE"), nullable=True),
        sa.Column("account", sa.String(length=120), nullable=False, server_default=""),
        sa.Column("folder", sa.String(length=255), nullable=False, server_default=""),
        sa.Column("uid", sa.Integer(), nullable=True),
        sa.Column("sender_email", sa.String(length=320), nullable=False, server_default=""),
        sa.Column("sender_domain", sa.String(length=255), nullable=False, server_default=""),
        sa.Column("recipient", sa.String(length=320), nullable=False, server_default=""),
        sa.Column("subject", sa.String(length=500), nullable=False, server_default=""),
        sa.Column("rule_score", sa.Float(), nullable=False, server_default="0"),
        sa.Column("model_score", sa.Float(), nullable=False, server_default="0"),
        sa.Column("learned_score", sa.Float(), nullable=False, server_default="0"),
        sa.Column("score", sa.Float(), nullable=False, server_default="0"),
        sa.Column("reasons", sa.JSON(), nullable=True),
        sa.Column("features", sa.JSON(), nullable=True),
        sa.Column("status", sa.String(length=20), nullable=False, server_default="pending"),
        sa.Column("digest_batch", sa.String(length=32), nullable=True),
        sa.Column("decided_by", sa.String(length=20), nullable=False, server_default=""),
        sa.Column("decided_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("action_result", sa.Text(), nullable=False, server_default=""),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.func.now()),
    )
    for spalte in ("owner_user_id", "assistant_task_id", "sender_email", "sender_domain",
                   "recipient", "score", "status", "digest_batch"):
        op.create_index(f"ix_spam_verdicts_{spalte}", "spam_verdicts", [spalte])

    op.create_table(
        "spam_feature_stats",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("owner_user_id", sa.Integer(),
                  sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=True),
        sa.Column("feature", sa.String(length=400), nullable=False, server_default=""),
        sa.Column("spam_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("ham_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("last_seen_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.func.now()),
    )
    op.create_index("ix_spam_feature_stats_owner_user_id", "spam_feature_stats",
                    ["owner_user_id"])
    op.create_index("ix_spam_feature_stats_feature", "spam_feature_stats", ["feature"])
    op.create_unique_constraint("uq_spam_feature", "spam_feature_stats",
                                ["owner_user_id", "feature"])

    op.add_column("notifications", sa.Column("spam_verdict_id", sa.Integer(), nullable=True))
    op.create_foreign_key("fk_notifications_spam_verdict", "notifications", "spam_verdicts",
                          ["spam_verdict_id"], ["id"], ondelete="CASCADE")


def downgrade() -> None:
    op.drop_constraint("fk_notifications_spam_verdict", "notifications", type_="foreignkey")
    op.drop_column("notifications", "spam_verdict_id")

    op.drop_constraint("uq_spam_feature", "spam_feature_stats", type_="unique")
    op.drop_index("ix_spam_feature_stats_feature", table_name="spam_feature_stats")
    op.drop_index("ix_spam_feature_stats_owner_user_id", table_name="spam_feature_stats")
    op.drop_table("spam_feature_stats")

    for spalte in ("owner_user_id", "assistant_task_id", "sender_email", "sender_domain",
                   "recipient", "score", "status", "digest_batch"):
        op.drop_index(f"ix_spam_verdicts_{spalte}", table_name="spam_verdicts")
    op.drop_table("spam_verdicts")

    op.drop_constraint("uq_assistant_contact", "assistant_contacts", type_="unique")
    op.drop_index("ix_assistant_contacts_domain", table_name="assistant_contacts")
    op.drop_index("ix_assistant_contacts_email", table_name="assistant_contacts")
    op.drop_index("ix_assistant_contacts_owner_user_id", table_name="assistant_contacts")
    op.drop_table("assistant_contacts")
