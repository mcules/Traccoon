"""PM-Chat-Darstellung je Nutzer: users.pm_chat_style (TRA-21)

Revision ID: d9a3f6c14b72
Revises: c7f1b4e29a35
Create Date: 2026-07-25
"""
from alembic import op
import sqlalchemy as sa


revision = 'd9a3f6c14b72'
down_revision = 'c7f1b4e29a35'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        'users',
        sa.Column('pm_chat_style', sa.String(length=10), nullable=False, server_default='bubbles'),
    )


def downgrade() -> None:
    op.drop_column('users', 'pm_chat_style')
