"""one mail, one entry in a report

Revision ID: d5f13c806a92
Revises: c4e82a1b60d7
Create Date: 2026-08-28
"""
from alembic import op
import sqlalchemy as sa


revision = 'd5f13c806a92'
down_revision = 'c4e82a1b60d7'
branch_labels = None
depends_on = None


def upgrade() -> None:
    # A mail can be delivered twice at the same moment (the copy in Sent and the delivered
    # one, an inbox that is read again). Both look first and see nothing, then both write:
    # the check alone loses that race, the index does not.
    #
    # Partial, because entries without a Message-ID are the normal case — everything written
    # here in the house has none, and two of those are two entries.
    op.execute("DELETE FROM report_posts a USING report_posts b "
               "WHERE a.id > b.id AND a.message_id <> '' "
               "AND a.artifact_id = b.artifact_id AND a.message_id = b.message_id")
    op.create_index('uq_report_post_message', 'report_posts', ['artifact_id', 'message_id'],
                    unique=True, postgresql_where=sa.text("message_id <> ''"),
                    sqlite_where=sa.text("message_id <> ''"))


def downgrade() -> None:
    op.drop_index('uq_report_post_message', table_name='report_posts')
