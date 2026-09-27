"""Claude CLI sessions: projects.cli_mode, issue delivery options, cli_sessions, cli_deliveries

Revision ID: c3e9a7d15f22
Revises: b8d3f1a72c05
Create Date: 2026-09-27
"""
from alembic import op
import sqlalchemy as sa


revision = 'c3e9a7d15f22'
down_revision = 'b8d3f1a72c05'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column('projects', sa.Column('cli_mode', sa.Boolean(), nullable=False,
                                        server_default=sa.false()))
    op.add_column('issues', sa.Column('cli_delivery', sa.String(10), nullable=False,
                                      server_default='queue'))
    op.add_column('issues', sa.Column('cli_context', sa.String(10), nullable=False,
                                      server_default='keep'))
    op.create_table(
        'cli_sessions',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('project_id', sa.Integer(), sa.ForeignKey('projects.id', ondelete='CASCADE'),
                  nullable=False),
        sa.Column('user_id', sa.Integer(), sa.ForeignKey('users.id', ondelete='CASCADE'),
                  nullable=False),
        sa.Column('container', sa.String(120), nullable=False, server_default=''),
        sa.Column('status', sa.String(20), nullable=False, server_default='stopped'),
        sa.Column('error', sa.Text(), nullable=False, server_default=''),
        sa.Column('mcp_token_enc', sa.Text(), nullable=False, server_default=''),
        sa.Column('mcp_token_hash', sa.String(64), nullable=False, server_default=''),
        sa.Column('last_attach_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now(),
                  nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.func.now(),
                  nullable=False),
        sa.UniqueConstraint('project_id', 'user_id', name='uq_cli_session_project_user'),
    )
    op.create_index('ix_cli_sessions_project_id', 'cli_sessions', ['project_id'])
    op.create_index('ix_cli_sessions_user_id', 'cli_sessions', ['user_id'])
    op.create_index('ix_cli_sessions_mcp_token_hash', 'cli_sessions', ['mcp_token_hash'])
    op.create_table(
        'cli_deliveries',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('session_id', sa.Integer(),
                  sa.ForeignKey('cli_sessions.id', ondelete='CASCADE'), nullable=False),
        sa.Column('issue_id', sa.Integer(), sa.ForeignKey('issues.id', ondelete='CASCADE'),
                  nullable=False),
        sa.Column('task_id', sa.String(120), nullable=False, unique=True),
        sa.Column('delivery', sa.String(10), nullable=False, server_default='queue'),
        sa.Column('context', sa.String(10), nullable=False, server_default='keep'),
        sa.Column('position', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('state', sa.String(20), nullable=False, server_default='waiting'),
        sa.Column('error', sa.Text(), nullable=False, server_default=''),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column('delivered_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('reported_at', sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index('ix_cli_deliveries_session_id', 'cli_deliveries', ['session_id'])
    op.create_index('ix_cli_deliveries_issue_id', 'cli_deliveries', ['issue_id'])
    op.create_index('ix_cli_deliveries_state', 'cli_deliveries', ['state'])


def downgrade() -> None:
    op.drop_table('cli_deliveries')
    op.drop_table('cli_sessions')
    op.drop_column('issues', 'cli_context')
    op.drop_column('issues', 'cli_delivery')
    op.drop_column('projects', 'cli_mode')
