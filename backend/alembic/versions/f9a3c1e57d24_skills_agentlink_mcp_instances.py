"""autoload_skills plus the agent link plus McpServer.variables plus McpInstance

Revision ID: f9a3c1e57d24
Revises: e7c2f4a81b56
Create Date: 2026-07-19
"""
from alembic import op
import sqlalchemy as sa


revision = 'f9a3c1e57d24'
down_revision = 'e7c2f4a81b56'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column('agent_definitions', sa.Column('autoload_skills', sa.JSON(), nullable=False, server_default='[]'))
    op.add_column('agent_definitions', sa.Column('origin_agent_id', sa.Integer(), nullable=True))
    op.add_column('agent_definitions', sa.Column('customized', sa.Boolean(), nullable=False, server_default=sa.false()))
    op.create_foreign_key('fk_agent_origin', 'agent_definitions', 'agent_definitions',
                          ['origin_agent_id'], ['id'], ondelete='SET NULL')
    op.add_column('mcp_servers', sa.Column('variables', sa.JSON(), nullable=False, server_default='[]'))
    op.create_table(
        'mcp_instances',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('agent_id', sa.Integer(), sa.ForeignKey('agent_definitions.id', ondelete='CASCADE'), index=True),
        sa.Column('server_id', sa.Integer(), sa.ForeignKey('mcp_servers.id', ondelete='CASCADE'), index=True),
        sa.Column('name', sa.String(length=120), server_default=''),
        sa.Column('values_enc', sa.Text(), server_default=''),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now()),
    )
    op.create_index('ix_mcp_instances_agent_id', 'mcp_instances', ['agent_id'])
    op.create_index('ix_mcp_instances_server_id', 'mcp_instances', ['server_id'])


def downgrade() -> None:
    op.drop_table('mcp_instances')
    op.drop_column('mcp_servers', 'variables')
    op.drop_constraint('fk_agent_origin', 'agent_definitions', type_='foreignkey')
    op.drop_column('agent_definitions', 'customized')
    op.drop_column('agent_definitions', 'origin_agent_id')
    op.drop_column('agent_definitions', 'autoload_skills')
