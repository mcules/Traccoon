"""users.notify_destination_id — Benachrichtigung über ein Ziel

Telegram und E-Mail waren fest verdrahtet; jeder weitere Melder hätte Code gekostet. Ein
Ziel trägt Basis-URL und Anmeldung schon, also geht der dritte Kanal einfach dorthin — was
dahinter steckt (ntfy, Matrix, Gotify, ein eigener Bot), muss Traccoon nicht wissen.

Revision ID: d8b3c47f10a2
Revises: c4f7a92b13de
"""
import sqlalchemy as sa
from alembic import op

revision = 'd8b3c47f10a2'
down_revision = 'c4f7a92b13de'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column('users', sa.Column('notify_destination_id', sa.Integer(), nullable=True))
    op.create_foreign_key('fk_users_notify_destination', 'users', 'destinations',
                          ['notify_destination_id'], ['id'], ondelete='SET NULL')


def downgrade() -> None:
    op.drop_constraint('fk_users_notify_destination', 'users', type_='foreignkey')
    op.drop_column('users', 'notify_destination_id')
