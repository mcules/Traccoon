"""Artifacts without an ordering level, and fields per project instead

Ticket and hardware are both simply artifacts; the level above them ("process", "object")
contributed nothing and falls away again. An artifact is initially something undefined.

New is therefore `artifact_fields.project_id`: a project owner extends the shipped fields
with fields of their own, without changing those of other projects. The unique key is
rebuilt accordingly (`COALESCE`, because NULLs count as different in a unique index).

Revision ID: c1a7e94f5d32
Revises: b9f5c37a2e81
Create Date: 2026-07-26
"""
from alembic import op
import sqlalchemy as sa


revision = 'c1a7e94f5d32'
down_revision = 'b9f5c37a2e81'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("artifact_fields", sa.Column("project_id", sa.Integer(), nullable=True))
    op.create_foreign_key("fk_artifact_fields_project", "artifact_fields", "projects",
                          ["project_id"], ["id"], ondelete="CASCADE")
    op.create_index("ix_artifact_fields_project", "artifact_fields", ["project_id"])
    op.drop_constraint("uq_artifact_field", "artifact_fields", type_="unique")
    op.execute("""
        CREATE UNIQUE INDEX uq_artifact_field
        ON artifact_fields (type_id, COALESCE(project_id, 0), key)
    """)

    # The ordering level above the artifacts falls away again.
    op.drop_column("artifact_types", "group_id")
    op.drop_table("artifact_groups")


def downgrade() -> None:
    op.create_table(
        "artifact_groups",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("key", sa.String(40), nullable=False),
        sa.Column("name", sa.String(100), nullable=False),
        sa.Column("icon", sa.String(16), nullable=False, server_default="🗂️"),
        sa.Column("order", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("description", sa.Text(), nullable=False, server_default=""),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.UniqueConstraint("key", name="uq_artifact_group_key"),
    )
    op.add_column("artifact_types", sa.Column("group_id", sa.Integer(), nullable=True))
    op.create_foreign_key("fk_artifact_types_group", "artifact_types", "artifact_groups",
                          ["group_id"], ["id"], ondelete="SET NULL")

    # Project-owned fields make no sense without the column, so they are switched off instead
    # of deleted, in order to keep assigned values readable.
    op.execute("UPDATE artifact_fields SET enabled = FALSE WHERE project_id IS NOT NULL")
    op.drop_index("uq_artifact_field", table_name="artifact_fields")
    op.drop_index("ix_artifact_fields_project", table_name="artifact_fields")
    op.drop_constraint("fk_artifact_fields_project", "artifact_fields", type_="foreignkey")
    op.drop_column("artifact_fields", "project_id")
    op.create_unique_constraint("uq_artifact_field", "artifact_fields", ["type_id", "key"])
