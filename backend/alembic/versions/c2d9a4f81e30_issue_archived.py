"""Issue-Archiv: archived/archived_at

Revision ID: c2d9a4f81e30
Revises: b1c7e3f92a04
Create Date: 2026-07-21
"""
import sqlalchemy as sa
from alembic import op


revision = 'c2d9a4f81e30'
down_revision = 'b1c7e3f92a04'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column('issues', sa.Column('archived', sa.Boolean(), nullable=False,
                                      server_default=sa.false()))
    op.add_column('issues', sa.Column('archived_at', sa.DateTime(timezone=True), nullable=True))
    op.create_index('ix_issues_archived', 'issues', ['archived'])


def downgrade() -> None:
    op.drop_index('ix_issues_archived', table_name='issues')
    op.drop_column('issues', 'archived_at')
    op.drop_column('issues', 'archived')
