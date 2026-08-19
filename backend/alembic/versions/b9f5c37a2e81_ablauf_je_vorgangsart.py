"""The issue type chooses the process

Until now every ticket of a project ran the same lifecycle. A project copy may now be bound
to an issue type: a bug gets a flow of its own, while task and requirement keep following the
set.

Resolution (services/workflow_sets.resolve_definition):
    issue type -> project-owned (generic) -> set -> owner set -> default

The unique index is rebuilt for that. `COALESCE(issue_type_id, 0)` is necessary because NULLs
count as different in a unique index; otherwise any number of generic copies per slot arise.

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
    # Flows that hang off an issue type make no sense without the column, so they are archived
    # instead of deleted, in order to keep running instances readable.
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
