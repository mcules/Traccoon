"""Process sets: workflow_sets plus slot and archive on definitions, a set reference on project and user

Makes all flows (ticket lifecycle, acceptance, procurement, inbox) editable graphs: a set
holds one template per slot, projects reference a set and only create a copy of their own on
adjusting (copy-on-write).

Revision ID: a1c7e94f30b2
Revises: f4b9d2e60a18
Create Date: 2026-07-26
"""
from alembic import op
import sqlalchemy as sa


revision = 'a1c7e94f30b2'
down_revision = 'f4b9d2e60a18'
branch_labels = None
depends_on = None


def upgrade() -> None:
    workflowsetscope = sa.Enum('global', 'user', name='workflowsetscope')
    workflowsetscope.create(op.get_bind(), checkfirst=True)

    op.create_table(
        'workflow_sets',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column('scope', workflowsetscope, nullable=False, server_default='user'),
        sa.Column('user_id', sa.Integer(), sa.ForeignKey('users.id', ondelete='CASCADE'),
                  nullable=True, index=True),
        sa.Column('key', sa.String(length=60), nullable=False),
        sa.Column('name', sa.String(length=200), nullable=False),
        sa.Column('description', sa.Text(), nullable=False, server_default=''),
        sa.Column('is_builtin', sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column('builtin_revision', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('created_by', sa.Integer(), sa.ForeignKey('users.id', ondelete='SET NULL'),
                  nullable=True),
        sa.UniqueConstraint('user_id', 'key', name='uq_workflow_set_user_key'),
    )
    op.create_index('ix_workflow_sets_scope', 'workflow_sets', ['scope'])

    op.add_column('workflow_definitions', sa.Column(
        'set_id', sa.Integer(), sa.ForeignKey('workflow_sets.id', ondelete='CASCADE'), nullable=True))
    op.add_column('workflow_definitions', sa.Column('slot', sa.String(length=40), nullable=True))
    op.add_column('workflow_definitions', sa.Column(
        'archived_at', sa.DateTime(timezone=True), nullable=True))
    op.create_index('ix_workflow_definitions_set_id', 'workflow_definitions', ['set_id'])
    op.create_index('ix_workflow_definitions_slot', 'workflow_definitions', ['slot'])
    # At most ONE active flow per slot per set respectively per project; archived (reset)
    # copies are kept so that running instances stay intact.
    op.create_index('uq_workflow_def_set_slot', 'workflow_definitions', ['set_id', 'slot'],
                    unique=True, postgresql_where=sa.text('archived_at IS NULL'))
    op.create_index('uq_workflow_def_project_slot', 'workflow_definitions', ['project_id', 'slot'],
                    unique=True, postgresql_where=sa.text('archived_at IS NULL'))

    op.add_column('workflow_instances', sa.Column(
        'parent_instance_id', sa.Integer(),
        sa.ForeignKey('workflow_instances.id', ondelete='SET NULL'), nullable=True))
    op.add_column('workflow_instances', sa.Column('parent_node_id', sa.String(length=80), nullable=True))
    op.create_index('ix_workflow_instances_parent', 'workflow_instances', ['parent_instance_id'])

    op.add_column('workflow_step_runs', sa.Column(
        'routed_at', sa.DateTime(timezone=True), nullable=True))
    # Existing data: all already finished steps count as routed; otherwise the engine would
    # translate them into an edge again after the update.
    op.execute("UPDATE workflow_step_runs SET routed_at = completed_at "
               "WHERE completed_at IS NOT NULL AND routed_at IS NULL")

    op.add_column('projects', sa.Column(
        'workflow_set_id', sa.Integer(), sa.ForeignKey('workflow_sets.id', ondelete='SET NULL'),
        nullable=True))
    op.add_column('users', sa.Column(
        'workflow_set_id', sa.Integer(), sa.ForeignKey('workflow_sets.id', ondelete='SET NULL'),
        nullable=True))
    op.add_column('issues', sa.Column('workflow_instance_id', sa.Integer(), nullable=True))
    op.create_index('ix_issues_workflow_instance_id', 'issues', ['workflow_instance_id'])

    # Assign existing procurement definitions to the slot (they are the project adjustment).
    op.execute("UPDATE workflow_definitions SET slot = 'hardware_procurement' "
               "WHERE key = 'hardware-beschaffung' AND slot IS NULL AND project_id IS NOT NULL")

    for value in ('wait_event', 'subflow'):
        op.execute(f"ALTER TYPE workflownodetype ADD VALUE IF NOT EXISTS '{value}'")


def downgrade() -> None:
    op.drop_index('ix_issues_workflow_instance_id', table_name='issues')
    op.drop_column('issues', 'workflow_instance_id')
    op.drop_column('users', 'workflow_set_id')
    op.drop_column('projects', 'workflow_set_id')
    op.drop_column('workflow_step_runs', 'routed_at')
    op.drop_index('ix_workflow_instances_parent', table_name='workflow_instances')
    op.drop_column('workflow_instances', 'parent_node_id')
    op.drop_column('workflow_instances', 'parent_instance_id')
    op.drop_index('uq_workflow_def_project_slot', table_name='workflow_definitions')
    op.drop_index('uq_workflow_def_set_slot', table_name='workflow_definitions')
    op.drop_index('ix_workflow_definitions_slot', table_name='workflow_definitions')
    op.drop_index('ix_workflow_definitions_set_id', table_name='workflow_definitions')
    op.drop_column('workflow_definitions', 'archived_at')
    op.drop_column('workflow_definitions', 'slot')
    op.drop_column('workflow_definitions', 'set_id')
    op.drop_table('workflow_sets')
    sa.Enum(name='workflowsetscope').drop(op.get_bind(), checkfirst=True)
    # Enum values (wait_event/subflow) cannot be removed in PG, so they stay.
