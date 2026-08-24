"""Personal access tokens

Revision ID: b7e2c94a03f1
Revises: f3c8d21a94e5
Create Date: 2026-08-24
"""
from alembic import op
import sqlalchemy as sa


revision = 'b7e2c94a03f1'
down_revision = 'f3c8d21a94e5'
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Two halves in two columns: `prefix` is what the row is looked up by (public, indexed),
    # `token_hash` is the Argon2 hash of the secret. A hash cannot be searched for, and
    # running Argon2 against every row per request would be a denial of service against
    # ourselves.
    op.create_table(
        'api_tokens',
        sa.Column('id', sa.Integer, primary_key=True),
        sa.Column('owner_user_id', sa.Integer,
                  sa.ForeignKey('users.id', ondelete='CASCADE'), nullable=False, index=True),
        sa.Column('name', sa.String(120), nullable=False, server_default=''),
        sa.Column('prefix', sa.String(16), nullable=False),
        sa.Column('token_hash', sa.String(255), nullable=False, server_default=''),
        sa.Column('scopes', sa.String(255), nullable=False, server_default=''),
        sa.Column('created_at', sa.DateTime(timezone=True),
                  server_default=sa.func.now(), nullable=False),
        sa.Column('last_used_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('expires_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('revoked_at', sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index('ix_api_tokens_prefix', 'api_tokens', ['prefix'], unique=True)


def downgrade() -> None:
    op.drop_index('ix_api_tokens_prefix', table_name='api_tokens')
    op.drop_table('api_tokens')
