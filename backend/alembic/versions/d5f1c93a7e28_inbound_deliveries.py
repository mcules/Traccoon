"""What arrives from outside is stored before it is worked on

Revision ID: d5f1c93a7e28
Revises: c4e7a2f81b60
"""
import sqlalchemy as sa
from alembic import op

revision = "d5f1c93a7e28"
down_revision = "c4e7a2f81b60"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "inbound_deliveries",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("channel", sa.String(30), nullable=False, server_default="webhook"),
        sa.Column("target", sa.String(120), nullable=False, server_default=""),
        sa.Column("route", sa.String(120), nullable=False, server_default=""),
        sa.Column("body", sa.LargeBinary(), nullable=False),
        sa.Column("headers", sa.JSON(), nullable=False, server_default="{}"),
        sa.Column("received_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.func.now()),
        sa.Column("status", sa.String(20), nullable=False, server_default="new"),
        sa.Column("attempts", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("last_error", sa.Text(), nullable=False, server_default=""),
        sa.Column("next_try_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("outcome", sa.String(500), nullable=False, server_default=""),
    )
    op.create_index("ix_inbound_deliveries_channel", "inbound_deliveries", ["channel"])
    op.create_index("ix_inbound_deliveries_target", "inbound_deliveries", ["target"])
    op.create_index("ix_inbound_deliveries_received_at", "inbound_deliveries", ["received_at"])
    op.create_index("ix_inbound_deliveries_status", "inbound_deliveries", ["status"])
    op.create_index("ix_inbound_deliveries_next_try_at", "inbound_deliveries", ["next_try_at"])


def downgrade() -> None:
    op.drop_table("inbound_deliveries")
