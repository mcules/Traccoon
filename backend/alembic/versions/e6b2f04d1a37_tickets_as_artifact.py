"""Tickets werden Artefakte: gemeinsame Identität + Zustand in `artifacts`

Wie zuvor bei der Hardware: `issues` bleibt die Detailtabelle (Board, Sprint, Plan, Agent,
Merge, Testumgebung) und zeigt per `artifact_id` auf die gemeinsame Zeile. `agent_status`
wird weiter mitgeschrieben, solange Oberfläche, Filter und Dispatcher darauf laufen.

Revision ID: e6b2f04d1a37
Revises: d5a1c93e7b28
Create Date: 2026-07-26
"""
from alembic import op
import sqlalchemy as sa


revision = 'e6b2f04d1a37'
down_revision = 'd5a1c93e7b28'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column('issues', sa.Column(
        'artifact_id', sa.Integer(), sa.ForeignKey('artifacts.id', ondelete='SET NULL'),
        nullable=True))
    op.create_index('uq_issue_artifact', 'issues', ['artifact_id'], unique=True,
                    postgresql_where=sa.text('artifact_id IS NOT NULL'))

    # Je Ticket eine Artefakt-Zeile. Die Zuordnung läuft über eine Hilfsspalte, damit sie
    # auch bei gleichen Titeln eindeutig bleibt.
    op.execute("ALTER TABLE artifacts ADD COLUMN IF NOT EXISTS _issue_id INTEGER")
    op.execute("""
        INSERT INTO artifacts (type_id, project_id, title, status_key, data,
                               created_at, updated_at, _issue_id)
        SELECT t.id, i.project_id, left(i.summary, 500),
               coalesce(i.agent_status::text, ''), '{}'::json, now(), now(), i.id
        FROM issues i
        JOIN artifact_types t ON t.key = 'ticket'
        WHERE i.artifact_id IS NULL
    """)
    op.execute("""
        UPDATE issues i SET artifact_id = a.id
        FROM artifacts a WHERE a._issue_id = i.id AND i.artifact_id IS NULL
    """)
    op.execute("ALTER TABLE artifacts DROP COLUMN IF EXISTS _issue_id")

    # Prozess-Instanzen eines Tickets auf das Artefakt umhängen.
    op.execute("""
        UPDATE workflow_instances w SET artifact_id = i.artifact_id
        FROM issues i WHERE w.issue_id = i.id AND w.artifact_id IS NULL
    """)


def downgrade() -> None:
    op.drop_index('uq_issue_artifact', table_name='issues')
    op.drop_column('issues', 'artifact_id')
    op.execute("DELETE FROM artifacts WHERE type_id IN "
               "(SELECT id FROM artifact_types WHERE key = 'ticket')")
