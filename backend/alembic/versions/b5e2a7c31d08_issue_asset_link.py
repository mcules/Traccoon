"""Ticket zu Hardware: issues.asset_id (ABC-25)

Revision ID: b5e2a7c31d08
Revises: a3d7c9b12f56
Create Date: 2026-07-25
"""
from alembic import op
import sqlalchemy as sa


revision = 'b5e2a7c31d08'
down_revision = 'a3d7c9b12f56'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column('issues', sa.Column('asset_id', sa.Integer(), nullable=True))
    op.create_index('ix_issues_asset_id', 'issues', ['asset_id'])
    op.create_foreign_key(
        'fk_issues_asset', 'issues', 'hardware_assets', ['asset_id'], ['id'], ondelete='SET NULL',
    )


def downgrade() -> None:
    op.drop_constraint('fk_issues_asset', 'issues', type_='foreignkey')
    op.drop_index('ix_issues_asset_id', table_name='issues')
    op.drop_column('issues', 'asset_id')
