"""Issue.merge_conflict_rounds — Loop-Bremse gegen Accept-Konflikt-Endlosschleife

Revision ID: a3b1c9d72f40
Revises: f1a2b3c4d5e6
Create Date: 2026-07-22
"""
import sqlalchemy as sa
from alembic import op

revision = 'a3b1c9d72f40'
down_revision = 'f1a2b3c4d5e6'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("issues", sa.Column(
        "merge_conflict_rounds", sa.Integer(), nullable=False, server_default="0"))
    op.alter_column("issues", "merge_conflict_rounds", server_default=None)


def downgrade() -> None:
    op.drop_column("issues", "merge_conflict_rounds")
