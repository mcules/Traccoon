"""ProviderToken + AgentDefinition Token/Fallback-Felder

Revision ID: d5a1b9c72e30
Revises: c3f8a1e40d92
Create Date: 2026-07-19
"""
from alembic import op
import sqlalchemy as sa


revision = 'd5a1b9c72e30'
down_revision = 'c3f8a1e40d92'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        'provider_tokens',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('user_id', sa.Integer(), sa.ForeignKey('users.id', ondelete='CASCADE'), index=True),
        sa.Column('provider', sa.String(length=50), nullable=False),
        sa.Column('name', sa.String(length=120), nullable=False),
        sa.Column('value_enc', sa.Text(), nullable=False),
        sa.Column('is_default', sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.UniqueConstraint('user_id', 'provider', 'name', name='uq_provider_token'),
    )
    op.create_index('ix_provider_tokens_user_id', 'provider_tokens', ['user_id'])
    for name, type_ in [
        ('token_name', sa.String(length=120)),
        ('fallback_model', sa.String(length=150)),
        ('fallback_token_name', sa.String(length=120)),
    ]:
        op.add_column('agent_definitions', sa.Column(name, type_, nullable=False, server_default=''))


def downgrade() -> None:
    for name in ('token_name', 'fallback_model', 'fallback_token_name'):
        op.drop_column('agent_definitions', name)
    op.drop_table('provider_tokens')
