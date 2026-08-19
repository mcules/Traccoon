"""users.timezone (was heißt hier 8 Uhr?)

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
    # Bis hierher rechnete der Server in UTC und an drei Stellen fest in Europe/Berlin. Ein
    # Cron-Job „0 8 * * *" lief damit um zehn, und das Nachtfenster galt für alle gleich.
    op.add_column('users', sa.Column('timezone', sa.String(64), nullable=False,
                                     server_default='Europe/Berlin'))


def downgrade() -> None:
    op.drop_column('users', 'timezone')
