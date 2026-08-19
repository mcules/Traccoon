"""The state becomes a field, and the real columns become fields as well

Until now there were two models side by side: `artifact_statuses` for the state and
`artifact_fields` for everything else. Both are the same thing: a choice field with a value list.

The states therefore wander into the value list of the field `status` (category and "waiting"
become properties of the value), and the grown columns of ticket and unit (priority, issue
type, sprint, serial number …) appear as built-in fields with `source` = the column name.
Writing keeps going into the real column; board, sprints and the AI lifecycle read there
unchanged.

That engine and board expect a field called `status` is carried by `builtin`: with built-in
fields the key, the type and the origin are locked.

Revision ID: a8d4e21c6b73
Revises: f7c3a15b8d49
Create Date: 2026-07-26
"""
from alembic import op
import sqlalchemy as sa


revision = 'a8d4e21c6b73'
down_revision = 'f7c3a15b8d49'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("artifact_fields", sa.Column("source", sa.String(40), nullable=False,
                                               server_default=""))
    op.add_column("artifact_fields", sa.Column("options_source", sa.String(30), nullable=False,
                                               server_default=""))
    op.add_column("artifact_fields", sa.Column("builtin", sa.Boolean(), nullable=False,
                                               server_default=sa.false()))
    op.add_column("artifact_field_options", sa.Column("category", sa.String(20), nullable=False,
                                                      server_default=""))
    op.add_column("artifact_field_options", sa.Column("waiting", sa.Boolean(), nullable=False,
                                                      server_default=sa.false()))

    # Take the states over: one field `status` per artifact, whose value list is the old rows.
    # Label, category and "waiting" are kept; whoever adjusted them keeps them.
    #
    op.execute("""
        INSERT INTO artifact_fields (type_id, "key", label, kind, multi, required, "order",
                                     description, enabled, source, options_source, builtin,
                                     created_at, updated_at)
        SELECT DISTINCT s.type_id, 'status', 'Status', 'select', FALSE, FALSE, 0, '', TRUE,
               CASE t.backing WHEN 'issue' THEN 'agent_status'
                              WHEN 'hardware_asset' THEN 'purchase_status' ELSE '' END,
               '', TRUE, now(), now()
        FROM artifact_statuses s
        JOIN artifact_types t ON t.id = s.type_id
        WHERE NOT EXISTS (SELECT 1 FROM artifact_fields f
                          WHERE f.type_id = s.type_id AND f."key" = 'status')
    """)
    op.execute("""
        INSERT INTO artifact_field_options (field_id, value, label, color, "order", enabled,
                                            category, waiting)
        SELECT f.id, s."key", s.label, '', s."order", TRUE,
               COALESCE(s.category, ''), COALESCE(s.waiting, FALSE)
        FROM artifact_statuses s
        JOIN artifact_fields f ON f.type_id = s.type_id AND f."key" = 'status'
        WHERE NOT EXISTS (SELECT 1 FROM artifact_field_options o
                          WHERE o.field_id = f.id AND o.value = s."key")
    """)
    op.drop_table("artifact_statuses")

    # The remaining built-in fields are created by the program start
    # (`services/artifact_fields.ensure_builtin_fields`): they stand in the code, so that the
    # schema and the directory cannot drift apart.


def downgrade() -> None:
    op.create_table(
        "artifact_statuses",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("type_id", sa.Integer(), nullable=False),
        sa.Column("key", sa.String(40), nullable=False),
        sa.Column("label", sa.String(100), nullable=False),
        sa.Column("category", sa.String(20), nullable=False, server_default="in_progress"),
        sa.Column("order", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("is_default", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("waiting", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.ForeignKeyConstraint(["type_id"], ["artifact_types.id"], ondelete="CASCADE"),
        sa.UniqueConstraint("type_id", "key", name="uq_artifact_status"),
    )
    op.execute("""
        INSERT INTO artifact_statuses (type_id, "key", label, category, "order", is_default, waiting)
        SELECT f.type_id, o.value, o.label, COALESCE(NULLIF(o.category, ''), 'in_progress'),
               o."order", FALSE, o.waiting
        FROM artifact_field_options o
        JOIN artifact_fields f ON f.id = o.field_id
        WHERE f."key" = 'status'
    """)
    op.execute("DELETE FROM artifact_fields WHERE builtin")
    op.drop_column("artifact_field_options", "waiting")
    op.drop_column("artifact_field_options", "category")
    op.drop_column("artifact_fields", "builtin")
    op.drop_column("artifact_fields", "options_source")
    op.drop_column("artifact_fields", "source")
