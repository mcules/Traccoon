"""webhook_subs.response_timeout/response_map (a flow answers its trigger)

Revision ID: e5a2c81f7b40
Revises: b4e1c7a92f60
Create Date: 2026-08-19
"""
from alembic import op
import sqlalchemy as sa


revision = 'e5a2c81f7b40'
down_revision = 'b4e1c7a92f60'
branch_labels = None
depends_on = None


def upgrade() -> None:
    # A webhook is a trigger — and a trigger may have an answer. How long the request stays
    # open, and which fields the answer carries; without a map the flow's own `antwort` goes
    # back (action `antwort`).
    op.add_column('webhook_subs', sa.Column('response_timeout', sa.Integer(),
                                            nullable=False, server_default='0'))
    op.add_column('webhook_subs', sa.Column('response_map', sa.JSON(),
                                            nullable=False, server_default='{}'))


def downgrade() -> None:
    op.drop_column('webhook_subs', 'response_map')
    op.drop_column('webhook_subs', 'response_timeout')
