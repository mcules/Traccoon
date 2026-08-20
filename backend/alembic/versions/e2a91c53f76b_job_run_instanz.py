"""job_runs.workflow_instance_id — der Lauf, den ein Job angestoßen hat

Damit trägt die Historie nach, was dabei herauskam: Ein Ablauf endet später als sein
Anstoß, und „Instanz #N gestartet“ ist kein Ergebnis. Zugleich der Schritt, mit dem die
Job-Arten `prompt`, `script` und `http` zu Knoten wurden (`services/job_modes.py` stellt
Bestehendes beim Start um); es bleiben `workflow` und `film`.

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
