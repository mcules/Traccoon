"""Workflow-Trigger: Webhook/Job → Workflow-Instanz

Revision ID: d7b3e1c50a92
Revises: c5f1a2d70b41
Create Date: 2026-07-24
"""
from alembic import op
import sqlalchemy as sa


revision = 'd7b3e1c50a92'
down_revision = 'c5f1a2d70b41'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column('webhook_subs', sa.Column('workflow_definition_id', sa.Integer(), nullable=True))
    op.add_column('webhook_subs', sa.Column('context_map', sa.JSON(), nullable=False,
                                            server_default='{}'))
    op.create_foreign_key('fk_webhook_workflow_def', 'webhook_subs', 'workflow_definitions',
                          ['workflow_definition_id'], ['id'], ondelete='SET NULL')
    op.add_column('jobs', sa.Column('workflow_definition_id', sa.Integer(), nullable=True))
    op.create_foreign_key('fk_job_workflow_def', 'jobs', 'workflow_definitions',
                          ['workflow_definition_id'], ['id'], ondelete='SET NULL')


def downgrade() -> None:
    op.drop_constraint('fk_job_workflow_def', 'jobs', type_='foreignkey')
    op.drop_column('jobs', 'workflow_definition_id')
    op.drop_constraint('fk_webhook_workflow_def', 'webhook_subs', type_='foreignkey')
    op.drop_column('webhook_subs', 'context_map')
    op.drop_column('webhook_subs', 'workflow_definition_id')
