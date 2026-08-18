"""Mail-Eingang als Prozess: Spam-Urteil kennt seinen Ablauf

Der Weg einer eingegangenen Mail (klassifizieren → beurteilen → nachfragen → verschieben)
läuft ab jetzt als Graph (Slot `mail_intake`, Auslöser `mail.received`). Damit die Antwort
aus Telegram den Ablauf weiterschaltet statt an ihm vorbei selbst zu verschieben, trägt die
Rückfrage ihre Instanz.

Alt-Urteile behalten NULL — für sie bleibt der direkte Weg (siehe `spam_review.entscheiden`).

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
