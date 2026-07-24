"""Workflow-Engine: Definitionen, Versionen, Instanzen, Tokens, Step-Runs

Revision ID: c5f1a2d70b41
Revises: b4e6f1a92c83
Create Date: 2026-07-24
"""
from alembic import op
import sqlalchemy as sa


revision = 'c5f1a2d70b41'
down_revision = 'b4e6f1a92c83'
branch_labels = None
depends_on = None


subject_kind = sa.Enum('issue', 'hardware_asset', 'standalone', name='workflowsubjectkind')
version_status = sa.Enum('draft', 'published', 'archived', name='workflowversionstatus')
instance_status = sa.Enum('running', 'waiting', 'completed', 'failed', 'cancelled',
                          name='workflowinstancestatus')
node_type = sa.Enum('start', 'end', 'human_task', 'decision', 'approval', 'auto_action',
                    'agent_task', name='workflownodetype')
token_state = sa.Enum('active', 'waiting', 'consumed', name='workflowtokenstate')
step_status = sa.Enum('pending', 'running', 'waiting', 'done', 'failed', 'skipped',
                      name='workflowstepstatus')


def upgrade() -> None:
    bind = op.get_bind()
    for e in (subject_kind, version_status, instance_status, node_type, token_state, step_status):
        e.create(bind, checkfirst=True)

    op.create_table(
        'workflow_definitions',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('project_id', sa.Integer(), sa.ForeignKey('projects.id', ondelete='SET NULL'), nullable=True),
        sa.Column('key', sa.String(length=60), nullable=False),
        sa.Column('name', sa.String(length=200), nullable=False),
        sa.Column('description', sa.Text(), nullable=False, server_default=''),
        sa.Column('subject_kind', subject_kind, nullable=False, server_default='standalone'),
        sa.Column('current_version_id', sa.Integer(), nullable=True),
        sa.Column('enabled', sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column('created_by', sa.Integer(), sa.ForeignKey('users.id', ondelete='SET NULL'), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint('project_id', 'key', name='uq_workflow_def_project_key'),
    )
    op.create_index('ix_workflow_definitions_project_id', 'workflow_definitions', ['project_id'])

    op.create_table(
        'workflow_versions',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('definition_id', sa.Integer(), sa.ForeignKey('workflow_definitions.id', ondelete='CASCADE'), nullable=False),
        sa.Column('version', sa.Integer(), nullable=False, server_default='1'),
        sa.Column('graph', sa.JSON(), nullable=False, server_default='{}'),
        sa.Column('status', version_status, nullable=False, server_default='draft'),
        sa.Column('notes', sa.Text(), nullable=False, server_default=''),
        sa.Column('created_by', sa.Integer(), sa.ForeignKey('users.id', ondelete='SET NULL'), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column('published_at', sa.DateTime(timezone=True), nullable=True),
        sa.UniqueConstraint('definition_id', 'version', name='uq_workflow_version'),
    )
    op.create_index('ix_workflow_versions_definition_id', 'workflow_versions', ['definition_id'])
    op.create_foreign_key('fk_workflow_def_current_version', 'workflow_definitions',
                          'workflow_versions', ['current_version_id'], ['id'], ondelete='SET NULL')

    op.create_table(
        'workflow_instances',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('definition_id', sa.Integer(), sa.ForeignKey('workflow_definitions.id', ondelete='CASCADE'), nullable=False),
        sa.Column('version_id', sa.Integer(), sa.ForeignKey('workflow_versions.id', ondelete='RESTRICT'), nullable=False),
        sa.Column('project_id', sa.Integer(), sa.ForeignKey('projects.id', ondelete='SET NULL'), nullable=True),
        sa.Column('subject_kind', subject_kind, nullable=False, server_default='standalone'),
        sa.Column('issue_id', sa.Integer(), sa.ForeignKey('issues.id', ondelete='SET NULL'), nullable=True),
        sa.Column('hardware_asset_id', sa.Integer(), sa.ForeignKey('hardware_assets.id', ondelete='SET NULL'), nullable=True),
        sa.Column('status', instance_status, nullable=False, server_default='running'),
        sa.Column('context', sa.JSON(), nullable=False, server_default='{}'),
        sa.Column('advancing', sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column('source', sa.String(length=64), nullable=True),
        sa.Column('source_ref', sa.String(length=255), nullable=True),
        sa.Column('error', sa.Text(), nullable=True),
        sa.Column('started_by', sa.Integer(), sa.ForeignKey('users.id', ondelete='SET NULL'), nullable=True),
        sa.Column('started_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column('finished_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index('ix_workflow_instances_definition_id', 'workflow_instances', ['definition_id'])
    op.create_index('ix_workflow_instances_project_id', 'workflow_instances', ['project_id'])
    op.create_index('ix_workflow_instances_issue_id', 'workflow_instances', ['issue_id'])
    op.create_index('ix_workflow_instances_hardware_asset_id', 'workflow_instances', ['hardware_asset_id'])
    op.create_index('ix_workflow_instances_status', 'workflow_instances', ['status'])

    op.create_table(
        'workflow_tokens',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('instance_id', sa.Integer(), sa.ForeignKey('workflow_instances.id', ondelete='CASCADE'), nullable=False),
        sa.Column('node_id', sa.String(length=80), nullable=False),
        sa.Column('state', token_state, nullable=False, server_default='active'),
        sa.Column('waiting_for', sa.String(length=30), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index('ix_workflow_tokens_instance_id', 'workflow_tokens', ['instance_id'])
    op.create_index('ix_workflow_tokens_state', 'workflow_tokens', ['state'])

    op.create_table(
        'workflow_step_runs',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('instance_id', sa.Integer(), sa.ForeignKey('workflow_instances.id', ondelete='CASCADE'), nullable=False),
        sa.Column('token_id', sa.Integer(), sa.ForeignKey('workflow_tokens.id', ondelete='SET NULL'), nullable=True),
        sa.Column('node_id', sa.String(length=80), nullable=False),
        sa.Column('node_type', node_type, nullable=False),
        sa.Column('status', step_status, nullable=False, server_default='pending'),
        sa.Column('assignee_user_id', sa.Integer(), sa.ForeignKey('users.id', ondelete='SET NULL'), nullable=True),
        sa.Column('form_data', sa.JSON(), nullable=True),
        sa.Column('decision', sa.String(length=60), nullable=True),
        sa.Column('result', sa.JSON(), nullable=True),
        sa.Column('agent_run_id', sa.Integer(), sa.ForeignKey('runs.id', ondelete='SET NULL'), nullable=True),
        sa.Column('error', sa.Text(), nullable=True),
        sa.Column('entered_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column('completed_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('completed_by', sa.Integer(), sa.ForeignKey('users.id', ondelete='SET NULL'), nullable=True),
    )
    op.create_index('ix_workflow_step_runs_instance_id', 'workflow_step_runs', ['instance_id'])
    op.create_index('ix_workflow_step_runs_assignee_user_id', 'workflow_step_runs', ['assignee_user_id'])
    op.create_index('ix_workflow_step_runs_status', 'workflow_step_runs', ['status'])


def downgrade() -> None:
    op.drop_table('workflow_step_runs')
    op.drop_table('workflow_tokens')
    op.drop_constraint('fk_workflow_def_current_version', 'workflow_definitions', type_='foreignkey')
    op.drop_table('workflow_instances')
    op.drop_table('workflow_versions')
    op.drop_table('workflow_definitions')
    bind = op.get_bind()
    for e in (step_status, token_state, node_type, instance_status, version_status, subject_kind):
        e.drop(bind, checkfirst=True)
