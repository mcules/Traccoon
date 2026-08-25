"""mail_image_rules (whose pictures may be fetched without asking)

Revision ID: d3f81a2c56b9
Revises: c9d4b17a3e58
Create Date: 2026-08-25
"""
from alembic import op
import sqlalchemy as sa


revision = 'd3f81a2c56b9'
down_revision = 'c9d4b17a3e58'
branch_labels = None
depends_on = None


def upgrade() -> None:
    # On the person and not on the account: mail from the same sender arrives in both
    # mailboxes, and the answer would be the same in both.
    op.create_table(
        'mail_image_rules',
        sa.Column('id', sa.Integer, primary_key=True),
        sa.Column('owner_user_id', sa.Integer,
                  sa.ForeignKey('users.id', ondelete='CASCADE'), nullable=False, index=True),
        sa.Column('kind', sa.String(10), nullable=False),
        sa.Column('value', sa.String(320), nullable=False, server_default=''),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.UniqueConstraint('owner_user_id', 'kind', 'value', name='uq_mail_image_rule'),
    )


def downgrade() -> None:
    op.drop_table('mail_image_rules')
