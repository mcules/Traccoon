"""artifact_field_options.description (what a value means for whoever works on the ticket),
users.cli_remote_control

Revision ID: d5a8e3c91b47
Revises: c3e9a7d15f22
Create Date: 2026-09-27
"""
from alembic import op
import sqlalchemy as sa


revision = 'd5a8e3c91b47'
down_revision = 'c3e9a7d15f22'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column('artifact_field_options', sa.Column('description', sa.Text(), nullable=False,
                                                      server_default=''))
    op.add_column('users', sa.Column('cli_remote_control', sa.Boolean(), nullable=False,
                                     server_default=sa.true()))


def downgrade() -> None:
    op.drop_column('users', 'cli_remote_control')
    op.drop_column('artifact_field_options', 'description')
