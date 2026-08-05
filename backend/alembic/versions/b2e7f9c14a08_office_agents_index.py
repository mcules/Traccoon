"""Personalakte: Index auf (agent, started_at) für die Rollen-Kennzahlen

Die Personalakte des Büros (`GET /office/agents`) rechnet fünf gruppierte Abfragen, und
jede einzelne gruppiert nach `runs.agent` innerhalb eines Zeitfensters — die
Werkzeugtabelle joint dafür sogar `run_steps` gegen `runs`. Bisher gab es Indizes auf
`(project_id, started_at)`, `(owner_id, started_at)` und `(issue_id, started_at)`, aber
keinen auf der Rolle: die Akte wäre ein Seq-Scan über inzwischen 13 000 Laufzeilen,
und zwar bei jedem Öffnen des Reiters.

`started_at DESC` steht mit im Index, weil das Fenster (`since_hours`) immer die jüngsten
Läufe meint — die Sortierrichtung erspart Postgres den Rückwärtslauf.

Der Live-Pfad ist `main.py::dev_create_all` (dort steht dasselbe DDL idempotent);
diese Revision ist der Pfad für `MIGRATE=1`.

Revision ID: b2e7f9c14a08
Revises: a1d47f8c9b02
Create Date: 2026-08-05
"""
from alembic import op
import sqlalchemy as sa


revision = 'b2e7f9c14a08'
down_revision = 'a1d47f8c9b02'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_index('ix_runs_agent_started', 'runs',
                    ['agent', sa.text('started_at DESC')], unique=False)


def downgrade() -> None:
    op.drop_index('ix_runs_agent_started', table_name='runs')
