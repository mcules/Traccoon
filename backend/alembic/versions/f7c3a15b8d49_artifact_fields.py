"""Fields and values on artifacts (the Artefakt model: Artifacts → Fields → Values)

An artifact (ticket, hardware, own type) carries typed fields; a choice field has a
maintained value list, and `multi` says whether a unit may carry one or several values from
it. The values hang off `artifacts.id`, the common identity ticket and hardware have anyway.

Above that, `artifact_groups` adds a pure ordering level ("process" over ticket and bug).

The two JSON placeholders `artifact_types.fields` and `artifacts.data` fall away: they were
never filled and are superseded by the real model.

Revision ID: f7c3a15b8d49
Revises: e6b2f04d1a37
Create Date: 2026-07-26
"""
from alembic import op
import sqlalchemy as sa


revision = 'f7c3a15b8d49'
down_revision = 'e6b2f04d1a37'
branch_labels = None
depends_on = None


def upgrade() -> None:
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
    op.create_index("ix_artifact_groups_key", "artifact_groups", ["key"])

    op.add_column("artifact_types", sa.Column("group_id", sa.Integer(), nullable=True))
    op.create_foreign_key("fk_artifact_types_group", "artifact_types", "artifact_groups",
                          ["group_id"], ["id"], ondelete="SET NULL")
    op.create_index("ix_artifact_types_group", "artifact_types", ["group_id"])

    op.create_table(
        "artifact_fields",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("type_id", sa.Integer(), nullable=False),
        sa.Column("key", sa.String(40), nullable=False),
        sa.Column("label", sa.String(100), nullable=False),
        sa.Column("kind", sa.String(20), nullable=False, server_default="text"),
        sa.Column("multi", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("required", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("order", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("description", sa.Text(), nullable=False, server_default=""),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.ForeignKeyConstraint(["type_id"], ["artifact_types.id"], ondelete="CASCADE"),
        sa.UniqueConstraint("type_id", "key", name="uq_artifact_field"),
    )
    op.create_index("ix_artifact_fields_type", "artifact_fields", ["type_id"])

    op.create_table(
        "artifact_field_options",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("field_id", sa.Integer(), nullable=False),
        sa.Column("value", sa.String(200), nullable=False),
        sa.Column("label", sa.String(200), nullable=False, server_default=""),
        sa.Column("color", sa.String(20), nullable=False, server_default=""),
        sa.Column("order", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.ForeignKeyConstraint(["field_id"], ["artifact_fields.id"], ondelete="CASCADE"),
        sa.UniqueConstraint("field_id", "value", name="uq_artifact_field_option"),
    )
    op.create_index("ix_artifact_field_options_field", "artifact_field_options", ["field_id"])

    op.create_table(
        "artifact_values",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("artifact_id", sa.Integer(), nullable=False),
        sa.Column("field_id", sa.Integer(), nullable=False),
        sa.Column("option_id", sa.Integer(), nullable=True),
        sa.Column("value_text", sa.Text(), nullable=False, server_default=""),
        sa.Column("order", sa.Integer(), nullable=False, server_default="0"),
        sa.ForeignKeyConstraint(["artifact_id"], ["artifacts.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["field_id"], ["artifact_fields.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["option_id"], ["artifact_field_options.id"], ondelete="CASCADE"),
    )
    op.create_index("ix_artifact_values_artifact", "artifact_values", ["artifact_id"])
    op.create_index("ix_artifact_values_field", "artifact_values", ["field_id"])
    op.create_index("ix_artifact_values_option", "artifact_values", ["option_id"])

    # Never filled JSON placeholders, superseded by the model above.
    op.drop_column("artifact_types", "fields")
    op.drop_column("artifacts", "data")


def downgrade() -> None:
    op.add_column("artifacts", sa.Column("data", sa.JSON(), nullable=False,
                                         server_default="{}"))
    op.add_column("artifact_types", sa.Column("fields", sa.JSON(), nullable=False,
                                              server_default="[]"))
    op.drop_table("artifact_values")
    op.drop_table("artifact_field_options")
    op.drop_table("artifact_fields")
    op.drop_index("ix_artifact_types_group", table_name="artifact_types")
    op.drop_constraint("fk_artifact_types_group", "artifact_types", type_="foreignkey")
    op.drop_column("artifact_types", "group_id")
    op.drop_table("artifact_groups")
