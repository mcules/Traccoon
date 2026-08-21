"""users.timezone (what does 8 o'clock mean here?)

Revision ID: f7c3d21a95e4
Revises: e5a2c81f7b40
Create Date: 2026-08-19
"""
from alembic import op
import sqlalchemy as sa


revision = 'f7c3d21a95e4'
down_revision = 'e5a2c81f7b40'
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Until here the server computed in UTC and in three places hard-wired in Europe/Berlin. A
    # cron job "0 8 * * *" therefore ran at ten, and the night window applied to everyone alike.
    op.add_column('users', sa.Column('timezone', sa.String(64), nullable=False,
                                     server_default='Europe/Berlin'))


def downgrade() -> None:
    op.drop_column('users', 'timezone')
