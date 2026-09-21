"""mail_correspondents (the address book of the recipient field)

Revision ID: b8d3f1a72c05
Revises: a7c4e2f91b30
Create Date: 2026-09-21
"""
from alembic import op
import sqlalchemy as sa


revision = 'b8d3f1a72c05'
down_revision = 'a7c4e2f91b30'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        'mail_correspondents',
        sa.Column('id', sa.Integer, primary_key=True),
        sa.Column('account_id', sa.Integer,
                  sa.ForeignKey('mail_accounts.id', ondelete='CASCADE'), nullable=False,
                  index=True),
        sa.Column('email', sa.String(320), nullable=False, index=True),
        sa.Column('name', sa.String(300), nullable=False, server_default=''),
        sa.Column('sent', sa.Integer, nullable=False, server_default='0'),
        sa.Column('received', sa.Integer, nullable=False, server_default='0'),
        sa.Column('last_seen', sa.DateTime(timezone=True), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.func.now()),
        # One row per address and mailbox; the counts add up in it.
        sa.UniqueConstraint('account_id', 'email', name='uq_mail_correspondent'),
    )


def downgrade() -> None:
    op.drop_table('mail_correspondents')
