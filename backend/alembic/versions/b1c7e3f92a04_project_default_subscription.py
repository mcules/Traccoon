"""Projekt-Standard-Subscription: default_provider/default_token_name

Revision ID: b1c7e3f92a04
Revises: a9f4c2b81e07
Create Date: 2026-07-21
"""
import sqlalchemy as sa
from alembic import op


revision = 'b1c7e3f92a04'
down_revision = 'a9f4c2b81e07'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column('projects', sa.Column('default_provider', sa.String(length=50),
                                        nullable=False, server_default=''))
    op.add_column('projects', sa.Column('default_token_name', sa.String(length=120),
                                        nullable=False, server_default=''))


def downgrade() -> None:
    op.drop_column('projects', 'default_token_name')
    op.drop_column('projects', 'default_provider')
