"""User MCP-Gateway-Felder (harte Per-User-Trennung)

Revision ID: e7c2f4a81b56
Revises: d5a1b9c72e30
Create Date: 2026-07-19
"""
from alembic import op
import sqlalchemy as sa


revision = 'e7c2f4a81b56'
down_revision = 'd5a1b9c72e30'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column('users', sa.Column('mcp_group', sa.String(length=120), nullable=False, server_default=''))
    op.add_column('users', sa.Column('mcp_token_enc', sa.String(), nullable=False, server_default=''))
    op.add_column('users', sa.Column('mcp_servers', sa.JSON(), nullable=False, server_default='[]'))


def downgrade() -> None:
    op.drop_column('users', 'mcp_servers')
    op.drop_column('users', 'mcp_token_enc')
    op.drop_column('users', 'mcp_group')
