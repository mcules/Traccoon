"""A sender rule can block, and it says where it came from

Revision ID: c4e7a2f81b60
Revises: b7d1e4f92a03
"""
import sqlalchemy as sa
from alembic import op

revision = "c4e7a2f81b60"
down_revision = "b7d1e4f92a03"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("assistant_policies",
                  sa.Column("blocked", sa.Boolean(), nullable=False, server_default=sa.false()))
    op.add_column("assistant_policies",
                  sa.Column("origin", sa.String(300), nullable=False, server_default=""))
    op.add_column("assistant_policies", sa.Column("origin_task_id", sa.Integer(), nullable=True))


def downgrade() -> None:
    op.drop_column("assistant_policies", "origin_task_id")
    op.drop_column("assistant_policies", "origin")
    op.drop_column("assistant_policies", "blocked")
