"""the columns the chain never added

The backend adds missing columns from the models when it starts, which is what
kept every running database correct — and what let the migrations fall behind
without anybody noticing. A database built from the migrations alone was missing
these, so the two ways of arriving at a schema had drifted apart.

They are added here in one place rather than woven back into the revisions that
should have carried them: those revisions have run everywhere long ago, and
changing what they do would be rewriting a history that other machines already
have.

Revision ID: 36f10191080a
Revises: eb0c798a133d
Create Date: 2026-09-03
"""
from alembic import op
import sqlalchemy as sa


revision = '36f10191080a'
down_revision = 'eb0c798a133d'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.alter_column('chat_summaries', 'bis_task_id', new_column_name='to_task_id')
    op.add_column('cost_entries', sa.Column('priced', sa.Boolean, nullable=True))
    op.add_column('deployments', sa.Column('source', sa.String(20), nullable=False, server_default=''))
    op.add_column('deployments', sa.Column('announced_status', sa.String(20), nullable=False, server_default=''))
    op.add_column('issues', sa.Column('cap_baseline_run_id', sa.Integer, nullable=True))
    op.add_column('issues', sa.Column('review_rounds', sa.Integer, nullable=False, server_default='0'))
    op.add_column('mail_accounts', sa.Column('mcp_instructions', sa.Text, nullable=False, server_default=''))
    op.add_column('notifications', sa.Column('assistant_task_id', sa.Integer, sa.ForeignKey('assistant_tasks.id', ondelete='CASCADE'), nullable=True))
    op.alter_column('notifications', 'drossel_key', new_column_name='throttle_key')
    op.add_column('plugins', sa.Column('reads', sa.JSON, nullable=False, server_default=sa.text("'{}'::json")))
    op.add_column('plugins', sa.Column('reads_granted', sa.JSON, nullable=False, server_default=sa.text("'{}'::json")))
    op.add_column('plugins', sa.Column('csp', sa.JSON, nullable=False, server_default=sa.text("'{}'::json")))
    op.add_column('provider_tokens', sa.Column('base_url', sa.String(500), nullable=True))
    op.add_column('run_steps', sa.Column('kind', sa.String(24), nullable=False, server_default=''))
    op.add_column('run_steps', sa.Column('tool_use_id', sa.String(64), nullable=True))
    op.add_column('run_steps', sa.Column('target', sa.String(500), nullable=True))
    op.add_column('run_steps', sa.Column('ok', sa.Boolean, nullable=True))
    op.add_column('run_steps', sa.Column('duration_ms', sa.Integer, nullable=True))
    op.add_column('run_steps', sa.Column('in_tokens', sa.Integer, nullable=False, server_default='0'))
    op.add_column('run_steps', sa.Column('out_tokens', sa.Integer, nullable=False, server_default='0'))
    op.add_column('run_steps', sa.Column('cache_read_tokens', sa.Integer, nullable=False, server_default='0'))
    op.add_column('run_steps', sa.Column('provider', sa.String(50), nullable=True))
    op.add_column('run_steps', sa.Column('model', sa.String(150), nullable=True))
    op.add_column('runs', sa.Column('project_id', sa.Integer, sa.ForeignKey('projects.id', ondelete='SET NULL'), nullable=True))
    op.create_index('ix_runs_project_id', 'runs', ['project_id'])
    op.add_column('runs', sa.Column('owner_id', sa.Integer, sa.ForeignKey('users.id', ondelete='SET NULL'), nullable=True))
    op.create_index('ix_runs_owner_id', 'runs', ['owner_id'])
    op.add_column('runs', sa.Column('parent_tool_use_id', sa.String(64), nullable=True))
    op.add_column('runs', sa.Column('spawn_depth', sa.Integer, nullable=False, server_default='0'))
    op.add_column('runs', sa.Column('blocker_kind', sa.String(24), nullable=True))
    op.alter_column('spam_verdicts', 'art', new_column_name='kind')
    op.create_index('ix_spam_verdicts_kind', 'spam_verdicts', ['kind'])
    op.alter_column('spam_verdicts', 'befunde', new_column_name='findings')
    op.add_column('users', sa.Column('locale', sa.String(10), nullable=False, server_default='en'))
    op.add_column('users', sa.Column('assistant_notify', sa.String(10), nullable=False, server_default='needed'))
    op.add_column('users', sa.Column('list_sort', sa.JSON, nullable=False, server_default=sa.text("'{}'::json")))
    op.add_column('users', sa.Column('night_start_hour', sa.Integer, nullable=False, server_default='22'))
    op.add_column('users', sa.Column('night_end_hour', sa.Integer, nullable=False, server_default='6'))
    op.add_column('users', sa.Column('night_days', sa.JSON, nullable=False,
                        server_default=sa.text("'[0,1,2,3,4,5,6]'::json")))
    op.add_column('users', sa.Column('night_override', sa.Boolean, nullable=False, server_default=sa.false()))
    op.add_column('webhook_subs', sa.Column('classify_agent', sa.String(100), nullable=True))
    op.add_column('webhook_subs', sa.Column('event_name', sa.String(120), nullable=True))
    op.add_column('webhook_subs', sa.Column('prompt_tmpl', sa.Text, nullable=True))
    op.add_column('webhook_subs', sa.Column('auto_run', sa.Boolean, nullable=False, server_default=sa.false()))


def downgrade() -> None:
    # Deliberately not written out: this revision closes a gap, and going back
    # through it would take away columns the application has been writing to on
    # every machine for months.
    raise NotImplementedError("this one only goes forward")
