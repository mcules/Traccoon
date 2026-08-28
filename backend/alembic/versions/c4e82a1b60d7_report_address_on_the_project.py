"""the answering address sits on the project, and a reporting program needs one

Revision ID: c4e82a1b60d7
Revises: b3d71f0a52c4
Create Date: 2026-08-28
"""
from alembic import op
import sqlalchemy as sa


revision = 'c4e82a1b60d7'
down_revision = 'b3d71f0a52c4'
branch_labels = None
depends_on = None


def upgrade() -> None:
    # The mailbox is borrowed, the address is written: no login, no password, no server is
    # repeated on the project.
    op.add_column('projects', sa.Column('mail_account_id', sa.Integer(), nullable=True))
    op.create_foreign_key('fk_projects_mail_account', 'projects', 'mail_accounts',
                          ['mail_account_id'], ['id'], ondelete='SET NULL')
    op.create_index('ix_projects_mail_account_id', 'projects', ['mail_account_id'])
    op.add_column('projects', sa.Column('reply_from', sa.String(320), nullable=False,
                                        server_default=''))
    op.add_column('projects', sa.Column('reply_name', sa.String(120), nullable=False,
                                        server_default=''))
    op.add_column('projects', sa.Column('answer_agent', sa.String(100), nullable=False,
                                        server_default=''))

    # The address was on the reporting program for one migration. It never got a value —
    # this is the same setting one step further out, not a second one.
    op.drop_index('ix_bug_sources_reply_identity_id', table_name='bug_sources')
    op.drop_constraint('fk_bug_sources_reply_identity', 'bug_sources', type_='foreignkey')
    op.drop_column('bug_sources', 'reply_identity_id')

    # A reporting program without a project reports into nothing: no address to answer from,
    # no board for the ticket. Every existing one has a project, so nothing has to be
    # invented here — the column only stops the next one from being created without.
    op.alter_column('bug_sources', 'project_id', existing_type=sa.Integer(), nullable=False)
    op.drop_constraint('bug_sources_project_id_fkey', 'bug_sources', type_='foreignkey')
    op.create_foreign_key('bug_sources_project_id_fkey', 'bug_sources', 'projects',
                          ['project_id'], ['id'], ondelete='RESTRICT')


def downgrade() -> None:
    op.drop_constraint('bug_sources_project_id_fkey', 'bug_sources', type_='foreignkey')
    op.create_foreign_key('bug_sources_project_id_fkey', 'bug_sources', 'projects',
                          ['project_id'], ['id'], ondelete='SET NULL')
    op.alter_column('bug_sources', 'project_id', existing_type=sa.Integer(), nullable=True)

    op.add_column('bug_sources', sa.Column('reply_identity_id', sa.Integer(), nullable=True))
    op.create_foreign_key('fk_bug_sources_reply_identity', 'bug_sources', 'mail_identities',
                          ['reply_identity_id'], ['id'], ondelete='SET NULL')
    op.create_index('ix_bug_sources_reply_identity_id', 'bug_sources', ['reply_identity_id'])

    op.drop_column('projects', 'answer_agent')
    op.drop_column('projects', 'reply_name')
    op.drop_column('projects', 'reply_from')
    op.drop_index('ix_projects_mail_account_id', table_name='projects')
    op.drop_constraint('fk_projects_mail_account', 'projects', type_='foreignkey')
    op.drop_column('projects', 'mail_account_id')
