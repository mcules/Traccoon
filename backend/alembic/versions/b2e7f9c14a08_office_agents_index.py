"""Personnel file: an index on (agent, started_at) for the role key figures

The personnel file of the office (`GET /office/agents`) computes five grouped queries, and
each of them groups by `runs.agent` within a time window; the tool table even joins
`run_steps` against `runs` for it. Until now there were indexes on `(project_id,
started_at)`, `(owner_id, started_at)` and `(issue_id, started_at)`, but none on the role:
the file would be a seq scan over meanwhile 13 000 run rows, on every opening of the tab.

`started_at DESC` stands in the index as well, because the window (`since_hours`) always
means the most recent runs, and the sort direction saves Postgres the backward run.

The live path is `main.py::dev_create_all` (the same DDL stands there idempotently); this
revision is the path for `MIGRATE=1`.

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
