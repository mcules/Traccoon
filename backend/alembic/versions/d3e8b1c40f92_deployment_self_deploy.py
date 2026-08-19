"""Deployment.self_deploy: a self-deploy only explicitly

Revision ID: d3e8b1c40f92
Revises: c2d9a4f81e30
Create Date: 2026-07-21
"""
import sqlalchemy as sa
from alembic import op


revision = 'd3e8b1c40f92'
down_revision = 'c2d9a4f81e30'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column('deployments', sa.Column('self_deploy', sa.Boolean(), nullable=False,
                                           server_default=sa.false()))


def downgrade() -> None:
    op.drop_column('deployments', 'self_deploy')
