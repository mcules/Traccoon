"""artifact_field_options.description: what a value means for whoever works on the ticket

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


def downgrade() -> None:
    op.drop_column('artifact_field_options', 'description')
