"""reports answerable by mail (identity per source, way and Message-ID per entry)

Revision ID: b3d71f0a52c4
Revises: e1a4c96d20b7
Create Date: 2026-08-28
"""
from alembic import op
import sqlalchemy as sa


revision = 'b3d71f0a52c4'
down_revision = 'e1a4c96d20b7'
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Which identity answers for this program. `SET NULL` and not `CASCADE`: deleting a
    # mailbox identity must not take the reporting program with it — it only loses its way
    # by mail.
    op.add_column('bug_sources', sa.Column('reply_identity_id', sa.Integer(), nullable=True))
    op.create_foreign_key('fk_bug_sources_reply_identity', 'bug_sources', 'mail_identities',
                          ['reply_identity_id'], ['id'], ondelete='SET NULL')
    op.create_index('ix_bug_sources_reply_identity_id', 'bug_sources', ['reply_identity_id'])

    # `web` for everything that exists: before this migration an entry could only be written
    # here or come from a program, and the program ones are recognisable by `external_ref`.
    # Correcting those is the second statement — a default cannot look at another column.
    op.add_column('report_posts',
                  sa.Column('via', sa.String(10), nullable=False, server_default='web'))
    op.add_column('report_posts',
                  sa.Column('message_id', sa.String(400), nullable=False, server_default=''))
    op.execute("UPDATE report_posts SET via = 'app' WHERE external_ref <> ''")
    # Looked up on every incoming mail (the `In-Reply-To` of the reply), so it gets an index.
    op.create_index('ix_report_posts_message_id', 'report_posts', ['message_id'])


def downgrade() -> None:
    op.drop_index('ix_report_posts_message_id', table_name='report_posts')
    op.drop_column('report_posts', 'message_id')
    op.drop_column('report_posts', 'via')
    op.drop_index('ix_bug_sources_reply_identity_id', table_name='bug_sources')
    op.drop_constraint('fk_bug_sources_reply_identity', 'bug_sources', type_='foreignkey')
    op.drop_column('bug_sources', 'reply_identity_id')
