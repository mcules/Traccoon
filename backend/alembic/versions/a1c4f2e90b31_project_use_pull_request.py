"""projects.use_pull_request

Revision ID: a1c4f2e90b31
Revises: 87d3312bcd2b
Create Date: 2026-07-19
"""
from alembic import op
import sqlalchemy as sa


revision = 'a1c4f2e90b31'
down_revision = '87d3312bcd2b'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column('projects', sa.Column('use_pull_request', sa.Boolean(), nullable=False,
                                        server_default=sa.false()))


def downgrade() -> None:
    op.drop_column('projects', 'use_pull_request')
