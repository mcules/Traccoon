"""projects.has_hardware

Revision ID: a2b8d3f04c19
Revises: f9a3c1e57d24
Create Date: 2026-07-19
"""
from alembic import op
import sqlalchemy as sa

revision = 'a2b8d3f04c19'
down_revision = 'f9a3c1e57d24'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column('projects', sa.Column('has_hardware', sa.Boolean(), nullable=False, server_default=sa.false()))


def downgrade() -> None:
    op.drop_column('projects', 'has_hardware')
