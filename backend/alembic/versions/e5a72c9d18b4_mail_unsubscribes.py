"""mail_unsubscribes (which subscriptions one got out of, when and how)

Revision ID: e5a72c9d18b4
Revises: d3f81a2c56b9
Create Date: 2026-08-25
"""
from alembic import op
import sqlalchemy as sa


revision = 'e5a72c9d18b4'
down_revision = 'd3f81a2c56b9'
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Per account and not per person: the same newsletter can arrive in two mailboxes, and
    # getting out of it in one says nothing about the other.
    op.create_table(
        'mail_unsubscribes',
        sa.Column('id', sa.Integer, primary_key=True),
        sa.Column('owner_user_id', sa.Integer,
                  sa.ForeignKey('users.id', ondelete='CASCADE'), nullable=False, index=True),
        sa.Column('account_id', sa.Integer,
                  sa.ForeignKey('mail_accounts.id', ondelete='CASCADE'), nullable=False,
                  index=True),
        sa.Column('key', sa.String(320), nullable=False),
        sa.Column('name', sa.String(320), nullable=False, server_default=''),
        sa.Column('sender', sa.String(320), nullable=False, server_default=''),
        sa.Column('list_id', sa.String(320), nullable=False, server_default=''),
        sa.Column('way', sa.String(20), nullable=False, server_default=''),
        sa.Column('detail', sa.String(500), nullable=False, server_default=''),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.UniqueConstraint('account_id', 'key', name='uq_mail_unsubscribe'),
    )


def downgrade() -> None:
    op.drop_table('mail_unsubscribes')
