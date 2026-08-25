"""Agent runs can be archived: runs.archived/archived_at

Revision ID: c7f1b4e29a35
Revises: b5e2a7c31d08
Create Date: 2026-07-25
"""
from alembic import op
import sqlalchemy as sa


revision = 'c7f1b4e29a35'
down_revision = 'b5e2a7c31d08'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        'runs',
        sa.Column('archived', sa.Boolean(), nullable=False, server_default=sa.false()),
    )
    op.add_column('runs', sa.Column('archived_at', sa.DateTime(timezone=True), nullable=True))
    op.create_index('ix_runs_archived', 'runs', ['archived'])
    # Existing data: pull the runs of already archived tickets along.
    op.execute(
        "UPDATE runs SET archived = TRUE, archived_at = COALESCE(issues.archived_at, now()) "
        "FROM issues WHERE runs.issue_id = issues.id AND issues.archived"
    )


def downgrade() -> None:
    op.drop_index('ix_runs_archived', table_name='runs')
    op.drop_column('runs', 'archived_at')
    op.drop_column('runs', 'archived')
