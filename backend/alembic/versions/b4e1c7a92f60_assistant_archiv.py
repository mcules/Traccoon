"""assistant_tasks.archived_at (chat and inbox items out of the view)

Revision ID: b4e1c7a92f60
Revises: a2d9f4b71c53
Create Date: 2026-08-19
"""
from alembic import op
import sqlalchemy as sa


revision = 'b4e1c7a92f60'
down_revision = 'a2d9f4b71c53'
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Archiving instead of deleting: the assistant learns from finished items (rules, spam
    # statistics), so a row has to survive its disappearance from the view.
    op.add_column('assistant_tasks', sa.Column('archived_at', sa.DateTime(timezone=True),
                                               nullable=True))
    op.create_index('ix_assistant_tasks_archived_at', 'assistant_tasks', ['archived_at'])


def downgrade() -> None:
    op.drop_index('ix_assistant_tasks_archived_at', table_name='assistant_tasks')
    op.drop_column('assistant_tasks', 'archived_at')
