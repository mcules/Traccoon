"""app_settings Key-Value-Store

Revision ID: e4f9c2a81b73
Revises: d3e8b1c40f92
Create Date: 2026-07-21
"""
import sqlalchemy as sa
from alembic import op


revision = 'e4f9c2a81b73'
down_revision = 'd3e8b1c40f92'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        'app_settings',
        sa.Column('key', sa.String(length=100), primary_key=True),
        sa.Column('value', sa.Text(), nullable=False, server_default=''),
    )


def downgrade() -> None:
    op.drop_table('app_settings')
