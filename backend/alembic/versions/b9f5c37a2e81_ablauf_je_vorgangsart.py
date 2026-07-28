"""Die Vorgangsart wählt den Prozess

Bis hierher fuhr jedes Ticket eines Projekts denselben Lebenszyklus. Eine Projekt-Kopie darf
jetzt an eine Vorgangsart gebunden sein: ein Bug bekommt einen eigenen Ablauf, Aufgabe und
Anforderung folgen weiter dem Satz.

Auflösung (services/workflow_sets.resolve_definition):
    Vorgangsart → projekteigen (allgemein) → Satz → Owner-Satz → Standard

Der Unique-Index wird dafür neu gezogen. `COALESCE(issue_type_id, 0)` ist nötig, weil NULLs
in einem Unique-Index als verschieden gelten — sonst entstünden beliebig viele allgemeine
Kopien je Slot.

Revision ID: b9f5c37a2e81
Revises: a8d4e21c6b73
Create Date: 2026-07-26
"""
from alembic import op
import sqlalchemy as sa


revision = 'b9f5c37a2e81'
down_revision = 'a8d4e21c6b73'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("workflow_definitions",
                  sa.Column("issue_type_id", sa.Integer(), nullable=True))
    op.create_foreign_key("fk_workflow_definitions_issue_type", "workflow_definitions",
                          "issue_types", ["issue_type_id"], ["id"], ondelete="CASCADE")
    op.create_index("ix_workflow_definitions_issue_type", "workflow_definitions",
                    ["issue_type_id"])
    op.drop_index("uq_workflow_def_project_slot", table_name="workflow_definitions")
    op.execute("""
        CREATE UNIQUE INDEX uq_workflow_def_project_slot
        ON workflow_definitions (project_id, slot, COALESCE(issue_type_id, 0))
        WHERE archived_at IS NULL
    """)


def downgrade() -> None:
    # Abläufe, die an einer Vorgangsart hängen, haben ohne die Spalte keinen Sinn mehr —
    # sie werden archiviert statt gelöscht, damit laufende Instanzen lesbar bleiben.
    op.execute("UPDATE workflow_definitions SET archived_at = now(), enabled = FALSE "
               "WHERE issue_type_id IS NOT NULL AND archived_at IS NULL")
    op.drop_index("uq_workflow_def_project_slot", table_name="workflow_definitions")
    op.create_index("uq_workflow_def_project_slot", "workflow_definitions",
                    ["project_id", "slot"], unique=True,
                    postgresql_where=sa.text("archived_at IS NULL"))
    op.drop_index("ix_workflow_definitions_issue_type", table_name="workflow_definitions")
    op.drop_constraint("fk_workflow_definitions_issue_type", "workflow_definitions",
                       type_="foreignkey")
    op.drop_column("workflow_definitions", "issue_type_id")
