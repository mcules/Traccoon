"""how far somebody has read the conversation of a report

Revision ID: e6a92d41f708
Revises: d5f13c806a92
Create Date: 2026-08-28
"""
from alembic import op
import sqlalchemy as sa


revision = 'e6a92d41f708'
down_revision = 'd5f13c806a92'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        'report_reads',
        sa.Column('id', sa.Integer, primary_key=True),
        sa.Column('user_id', sa.Integer, sa.ForeignKey('users.id', ondelete='CASCADE'),
                  nullable=False, index=True),
        sa.Column('artifact_id', sa.Integer, sa.ForeignKey('artifacts.id', ondelete='CASCADE'),
                  nullable=False, index=True),
        # The number of the last entry seen, not a timestamp: two deliveries in the same
        # second would otherwise be indistinguishable, and one of them stays unread for ever.
        sa.Column('last_post_id', sa.Integer, nullable=False, server_default='0'),
        sa.Column('seen_at', sa.DateTime(timezone=True), server_default=sa.func.now()),
        # One mark per person and report. Read is personal: that one of us has seen the
        # answer says nothing about whether the others know it.
        sa.UniqueConstraint('user_id', 'artifact_id', name='uq_report_read'),
    )

    # Everything that exists counts as read. The alternative would be a first look at a list
    # in which every report of the last year is new — which says nothing and trains the eye
    # to ignore the mark.
    op.execute("""
        INSERT INTO report_reads (user_id, artifact_id, last_post_id)
        SELECT u.id, p.artifact_id, MAX(p.id)
          FROM report_posts p CROSS JOIN users u
         GROUP BY u.id, p.artifact_id
    """)


def downgrade() -> None:
    op.drop_table('report_reads')
