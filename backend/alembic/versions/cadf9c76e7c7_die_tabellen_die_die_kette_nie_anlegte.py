"""the tables the chain never made, before the first one needs them

These seventeen exist in the database on every machine that has ever run this
application, because the backend creates missing tables from the models at
start. They were never in a migration, so a database built from the migrations alone
did not have them — and the very next revision after this one puts a foreign key
on one of them and stopped there.

Their order below is the order the foreign keys need, not the alphabet.

They are created here as they stood at this point in the chain: the columns that
later revisions add are deliberately absent, so those revisions still do what
they say.

Revision ID: cadf9c76e7c7
Revises: c8f4b1e70a29
Create Date: 2026-09-03
"""
from alembic import op
import sqlalchemy as sa


revision = 'cadf9c76e7c7'
down_revision = 'c8f4b1e70a29'
branch_labels = None
depends_on = None


def _stamps() -> list:
    return [
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.func.now()),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.func.now()),
    ]


def upgrade() -> None:
    # A work item of the personal assistant, standing above the projects. What
    # leaves the house is the redacted summary; the raw text stays here.
    # `archived_at` and `session_id` come with their own revisions further on.
    op.create_table(
        'assistant_tasks',
        sa.Column('id', sa.Integer, primary_key=True),
        sa.Column('owner_user_id', sa.Integer,
                  sa.ForeignKey('users.id', ondelete='CASCADE'), nullable=True, index=True),
        sa.Column('kind', sa.String(30), nullable=False, server_default='email'),
        sa.Column('source', sa.String(120), nullable=False, server_default=''),
        sa.Column('source_ref', sa.String(255), nullable=True, index=True),
        sa.Column('title', sa.String(500), nullable=False, server_default=''),
        sa.Column('category', sa.String(80), nullable=False, server_default=''),
        sa.Column('priority', sa.String(20), nullable=False, server_default='normal'),
        sa.Column('redacted_summary', sa.Text, nullable=False, server_default=''),
        sa.Column('meta', sa.JSON, nullable=False, server_default=sa.text("'{}'::json")),
        sa.Column('redaction', sa.String(20), nullable=False, server_default='redacted'),
        sa.Column('raw_body', sa.Text, nullable=True),
        sa.Column('action_hint', sa.Text, nullable=False, server_default=''),
        sa.Column('status', sa.String(20), nullable=False, server_default='new', index=True),
        sa.Column('run_id', sa.Integer, nullable=True),
        sa.Column('result', sa.Text, nullable=False, server_default=''),
        sa.Column('error', sa.Text, nullable=False, server_default=''),
        sa.Column('finished_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('pending_tool', sa.String(150), nullable=True),
        sa.Column('pending_resource', sa.String(500), nullable=True),
        sa.Column('grant_tool', sa.String(150), nullable=True),
        sa.Column('grant_resource', sa.String(500), nullable=True),
        sa.Column('notified', sa.Boolean, nullable=False, server_default=sa.false()),
        *_stamps(),
    )

    # A learned rule for incoming items. `blocked`, `origin` and `origin_task_id`
    # come with their own revision further on.
    op.create_table(
        'assistant_policies',
        sa.Column('id', sa.Integer, primary_key=True),
        sa.Column('owner_user_id', sa.Integer,
                  sa.ForeignKey('users.id', ondelete='CASCADE'), nullable=True, index=True),
        sa.Column('match_kind', sa.String(20), nullable=False, server_default='sender'),
        sa.Column('match_value', sa.String(300), nullable=False, server_default=''),
        sa.Column('auto_approve', sa.Boolean, nullable=False, server_default=sa.true()),
        sa.Column('redaction', sa.String(20), nullable=False, server_default='redacted'),
        sa.Column('action_hint', sa.Text, nullable=False, server_default=''),
        sa.Column('enabled', sa.Boolean, nullable=False, server_default=sa.true()),
        sa.Column('hit_count', sa.Integer, nullable=False, server_default='0'),
        sa.Column('last_used_at', sa.DateTime(timezone=True), nullable=True),
        *_stamps(),
    )

    # A measurement series. `still_at` comes with its own revision further on.
    op.create_table(
        'metric_series',
        sa.Column('id', sa.Integer, primary_key=True),
        sa.Column('owner_user_id', sa.Integer,
                  sa.ForeignKey('users.id', ondelete='CASCADE'), nullable=True, index=True),
        sa.Column('key', sa.String(120), nullable=False),
        sa.Column('name', sa.String(200), nullable=False, server_default=''),
        sa.Column('unit', sa.String(20), nullable=False, server_default=''),
        sa.Column('description', sa.Text, nullable=False, server_default=''),
        sa.Column('last_value', sa.Float, nullable=True),
        sa.Column('last_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('warned_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('warned_value', sa.Float, nullable=True),
        *_stamps(),
        sa.UniqueConstraint('owner_user_id', 'key', name='uq_metric_series_owner_key'),
    )

    # The rest: no revision touches these at all, except for the columns
    # noted above, which their own revisions add further on.
    op.create_table(
        'assistant_permissions',
        sa.Column('id', sa.Integer, primary_key=True),
        sa.Column('owner_user_id', sa.Integer, sa.ForeignKey('users.id', ondelete='CASCADE'), nullable=True, index=True),
        sa.Column('tool', sa.String(150), nullable=False, server_default=''),
        sa.Column('resource', sa.String(500), nullable=False, server_default='*'),
        sa.Column('action', sa.String(10), nullable=False, server_default='ask'),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False, server_default=sa.text('now()')),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False, server_default=sa.text('now()')),
        sa.UniqueConstraint('owner_user_id', 'tool', 'resource', name='uq_assistant_perm'),
    )
    op.create_table(
        'attachments',
        sa.Column('id', sa.Integer, primary_key=True),
        sa.Column('issue_id', sa.Integer, sa.ForeignKey('issues.id', ondelete='CASCADE'), nullable=False, index=True),
        sa.Column('uploader_id', sa.Integer, sa.ForeignKey('users.id', ondelete='SET NULL'), nullable=True),
        sa.Column('filename', sa.String(500), nullable=False),
        sa.Column('mime_type', sa.String(120), nullable=False, server_default='application/octet-stream'),
        sa.Column('size', sa.Integer, nullable=False, server_default='0'),
        sa.Column('data', sa.LargeBinary, nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False, server_default=sa.text('now()')),
    )
    op.create_table(
        'bug_sources',
        sa.Column('id', sa.Integer, primary_key=True),
        sa.Column('key', sa.String(40), nullable=False, index=True),
        sa.Column('name', sa.String(120), nullable=False),
        sa.Column('token_hash', sa.String(255), nullable=False, server_default=''),
        sa.Column('token_hint', sa.String(12), nullable=False, server_default=''),
        sa.Column('enabled', sa.Boolean, nullable=False, server_default=sa.true()),
        sa.Column('project_id', sa.Integer, sa.ForeignKey('projects.id', ondelete='RESTRICT'), nullable=False, index=True),
        sa.Column('hourly_limit', sa.Integer, nullable=False, server_default='20'),
        sa.Column('description', sa.Text, nullable=False, server_default=''),
        sa.Column('callback_url', sa.String(500), nullable=False, server_default=''),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False, server_default=sa.text('now()')),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False, server_default=sa.text('now()')),
    )
    op.create_table(
        'plugin_data',
        sa.Column('id', sa.Integer, primary_key=True),
        sa.Column('plugin_id', sa.Integer, sa.ForeignKey('plugins.id', ondelete='CASCADE'), nullable=False, index=True),
        sa.Column('table_name', sa.String(120), nullable=False, index=True),
        sa.Column('user_id', sa.Integer, sa.ForeignKey('users.id', ondelete='CASCADE'), nullable=True),
        sa.Column('row', sa.JSON, nullable=False, server_default=sa.text("'{}'::json")),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False, server_default=sa.text('now()')),
    )
    op.create_table(
        'report_posts',
        sa.Column('id', sa.Integer, primary_key=True),
        sa.Column('artifact_id', sa.Integer, sa.ForeignKey('artifacts.id', ondelete='CASCADE'), nullable=False, index=True),
        sa.Column('author_id', sa.Integer, sa.ForeignKey('users.id', ondelete='SET NULL'), nullable=True),
        sa.Column('author_label', sa.String(200), nullable=False, server_default=''),
        sa.Column('external_ref', sa.String(120), nullable=False, index=True, server_default=''),
        sa.Column('body', sa.Text, nullable=False),
        sa.Column('internal', sa.Boolean, nullable=False, server_default=sa.false()),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False, server_default=sa.text('now()')),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False, server_default=sa.text('now()')),
    )
    op.create_table(
        'report_images',
        sa.Column('id', sa.Integer, primary_key=True),
        sa.Column('post_id', sa.Integer, sa.ForeignKey('report_posts.id', ondelete='CASCADE'), nullable=True, index=True),
        sa.Column('artifact_id', sa.Integer, sa.ForeignKey('artifacts.id', ondelete='CASCADE'), nullable=True, index=True),
        sa.Column('filename', sa.String(300), nullable=False, server_default=''),
        sa.Column('mime_type', sa.String(120), nullable=False, server_default='image/png'),
        sa.Column('size', sa.Integer, nullable=False, server_default='0'),
        sa.Column('data', sa.LargeBinary, nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False, server_default=sa.text('now()')),
    )
    op.create_table(
        'series',
        sa.Column('id', sa.Integer, primary_key=True),
        sa.Column('owner_user_id', sa.Integer, sa.ForeignKey('users.id', ondelete='CASCADE'), nullable=True, index=True),
        sa.Column('key', sa.String(120), nullable=False),
        sa.Column('kind', sa.String(20), nullable=False, index=True, server_default='number'),
        sa.Column('name', sa.String(200), nullable=False, server_default=''),
        sa.Column('description', sa.Text, nullable=False, server_default=''),
        sa.Column('color', sa.String(7), nullable=False, server_default=''),
        sa.Column('settings', sa.JSON, nullable=False, server_default=sa.text("'{}'::json")),
        sa.Column('state', sa.JSON, nullable=False, server_default=sa.text("'{}'::json")),
        sa.Column('last_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('points', sa.Integer, nullable=False, server_default='0'),
        sa.Column('warned_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('warned_value', sa.Float, nullable=True),
        sa.Column('still_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('token_hash', sa.String(64), nullable=False, index=True, server_default=''),
        sa.Column('token_enc', sa.Text, nullable=False, server_default=''),
        sa.Column('active', sa.Boolean, nullable=False, server_default=sa.true()),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False, server_default=sa.text('now()')),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False, server_default=sa.text('now()')),
        sa.UniqueConstraint('owner_user_id', 'key', name='uq_series_owner_key'),
    )
    op.create_table(
        'series_places',
        sa.Column('id', sa.Integer, primary_key=True),
        sa.Column('owner_user_id', sa.Integer, sa.ForeignKey('users.id', ondelete='CASCADE'), nullable=True, index=True),
        sa.Column('series_id', sa.Integer, sa.ForeignKey('series.id', ondelete='CASCADE'), nullable=True, index=True),
        sa.Column('key', sa.String(120), nullable=False),
        sa.Column('name', sa.String(200), nullable=False, server_default=''),
        sa.Column('lat', sa.Float, nullable=False),
        sa.Column('lon', sa.Float, nullable=False),
        sa.Column('radius_m', sa.Integer, nullable=False, server_default='150'),
        sa.Column('color', sa.String(7), nullable=False, server_default=''),
        sa.Column('notify', sa.Boolean, nullable=False, server_default=sa.true()),
        sa.UniqueConstraint('owner_user_id', 'key', name='uq_series_place_owner_key'),
    )
    op.create_table(
        'series_points',
        sa.Column('id', sa.Integer, primary_key=True),
        sa.Column('series_id', sa.Integer, sa.ForeignKey('series.id', ondelete='CASCADE'), nullable=False, index=True),
        sa.Column('ts', sa.DateTime(timezone=True), nullable=False, index=True, server_default=sa.text('now()')),
        sa.Column('source', sa.String(30), nullable=False, server_default=''),
        sa.Column('context', sa.JSON, nullable=False, server_default=sa.text("'{}'::json")),
        sa.Column('value', sa.Float, nullable=True),
        sa.Column('lat', sa.Float, nullable=True),
        sa.Column('lon', sa.Float, nullable=True),
        sa.Column('extra', sa.JSON, nullable=False, server_default=sa.text("'{}'::json")),
        sa.Column('title', sa.String(200), nullable=False, server_default=''),
        sa.Column('body', sa.Text, nullable=False, server_default=''),
        sa.Column('format', sa.String(20), nullable=False, server_default=''),
    )
    op.create_table(
        'series_shares',
        sa.Column('id', sa.Integer, primary_key=True),
        sa.Column('series_id', sa.Integer, sa.ForeignKey('series.id', ondelete='CASCADE'), nullable=False, index=True),
        sa.Column('user_id', sa.Integer, sa.ForeignKey('users.id', ondelete='CASCADE'), nullable=False, index=True),
        sa.Column('level', sa.String(10), nullable=False, server_default='view'),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False, server_default=sa.text('now()')),
        sa.UniqueConstraint('series_id', 'user_id', name='uq_series_share'),
    )
    op.create_table(
        'metric_points',
        sa.Column('id', sa.Integer, primary_key=True),
        sa.Column('series_id', sa.Integer, sa.ForeignKey('metric_series.id', ondelete='CASCADE'), nullable=False, index=True),
        sa.Column('ts', sa.DateTime(timezone=True), nullable=False, index=True, server_default=sa.text('now()')),
        sa.Column('value', sa.Float, nullable=False),
        sa.Column('context', sa.JSON, nullable=False, server_default=sa.text("'{}'::json")),
    )
    op.create_table(
        'ticket_file_changes',
        sa.Column('id', sa.Integer, primary_key=True),
        sa.Column('issue_id', sa.Integer, sa.ForeignKey('issues.id', ondelete='CASCADE'), nullable=False, index=True),
        sa.Column('path', sa.String(1000), nullable=False),
        sa.Column('status', sa.String(20), nullable=False, server_default='modified'),
        sa.Column('additions', sa.Integer, nullable=False, server_default='0'),
        sa.Column('deletions', sa.Integer, nullable=False, server_default='0'),
        sa.Column('diff_text', sa.Text, nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False, server_default=sa.text('now()')),
    )
    op.create_table(
        'ui_locales',
        sa.Column('id', sa.Integer, primary_key=True),
        sa.Column('locale', sa.String(10), nullable=False, index=True),
        sa.Column('name', sa.String(80), nullable=False, server_default=''),
        sa.Column('enabled', sa.Boolean, nullable=False, server_default=sa.true()),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False, server_default=sa.text('now()')),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False, server_default=sa.text('now()')),
    )
    op.create_table(
        'ui_translations',
        sa.Column('id', sa.Integer, primary_key=True),
        sa.Column('locale', sa.String(10), nullable=False, index=True),
        sa.Column('key', sa.String(200), nullable=False),
        sa.Column('text', sa.Text, nullable=False, server_default=''),
        sa.Column('updated_by', sa.Integer, sa.ForeignKey('users.id', ondelete='SET NULL'), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False, server_default=sa.text('now()')),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False, server_default=sa.text('now()')),
        sa.UniqueConstraint('locale', 'key', name='uq_ui_translation'),
    )


def downgrade() -> None:
    op.drop_table('ui_translations')
    op.drop_table('ui_locales')
    op.drop_table('ticket_file_changes')
    op.drop_table('metric_points')
    op.drop_table('series_shares')
    op.drop_table('series_points')
    op.drop_table('series_places')
    op.drop_table('series')
    op.drop_table('report_images')
    op.drop_table('report_posts')
    op.drop_table('plugin_data')
    op.drop_table('bug_sources')
    op.drop_table('attachments')
    op.drop_table('assistant_permissions')
    op.drop_table('metric_series')
    op.drop_table('assistant_policies')
    op.drop_table('assistant_tasks')
