"""job_runs.workflow_instance_id — the run a job kicked off

With it the history fills in what came of it: a flow ends later than its kick-off, and
"instance #N started" is no result. At the same time the step with which the job kinds
`prompt`, `script` and `http` became nodes (`services/job_modes.py` converts existing ones at
startup); `workflow` and `film` remain.

Revision ID: e2a91c53f76b
Revises: d8b3c47f10a2
"""
import sqlalchemy as sa
from alembic import op

revision = 'e2a91c53f76b'
down_revision = 'd8b3c47f10a2'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column('job_runs', sa.Column('workflow_instance_id', sa.Integer(), nullable=True))
    op.create_foreign_key('fk_job_runs_instance', 'job_runs', 'workflow_instances',
                          ['workflow_instance_id'], ['id'], ondelete='SET NULL')


def downgrade() -> None:
    op.drop_constraint('fk_job_runs_instance', 'job_runs', type_='foreignkey')
    op.drop_column('job_runs', 'workflow_instance_id')
