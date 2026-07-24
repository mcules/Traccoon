"""users.ticket_layout (nutzerspezifische Ticket-Block-Anordnung)

Revision ID: a1d3e5f70c95
Revises: f9c2a3e10d84
Create Date: 2026-07-24
"""
from alembic import op
import sqlalchemy as sa


revision = 'a1d3e5f70c95'
down_revision = 'f9c2a3e10d84'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column('users', sa.Column('ticket_layout', sa.JSON(), nullable=False,
                                     server_default='{}'))


def downgrade() -> None:
    op.drop_column('users', 'ticket_layout')
