"""Responsible person per procurement step: hardware_workflow_steps.assignee (ABC-26)

Revision ID: e2c8b5d31f47
Revises: d9a3f6c14b72
Create Date: 2026-07-25
"""
from alembic import op
import sqlalchemy as sa


revision = 'e2c8b5d31f47'
down_revision = 'd9a3f6c14b72'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        'hardware_workflow_steps',
        sa.Column('assignee', sa.JSON(), nullable=False, server_default=sa.text("'{}'::json")),
    )


def downgrade() -> None:
    op.drop_column('hardware_workflow_steps', 'assignee')
