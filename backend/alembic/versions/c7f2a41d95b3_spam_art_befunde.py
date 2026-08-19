"""spam_verdicts.art + .befunde (what it was classified as, and why)

Revision ID: c7f2a41d95b3
Revises: b4e1c7a92f60
Create Date: 2026-08-19
"""
from alembic import op
import sqlalchemy as sa


revision = 'c7f2a41d95b3'
down_revision = 'b4e1c7a92f60'
branch_labels = None
depends_on = None


def upgrade() -> None:
    # The kind is indexed because the statistics group by it; the findings are read whole.
    op.add_column('spam_verdicts', sa.Column('art', sa.String(length=40),
                                             nullable=False, server_default=''))
    op.create_index('ix_spam_verdicts_art', 'spam_verdicts', ['art'])
    op.add_column('spam_verdicts', sa.Column('befunde', sa.JSON(),
                                             nullable=False, server_default='[]'))


def downgrade() -> None:
    op.drop_column('spam_verdicts', 'befunde')
    op.drop_index('ix_spam_verdicts_art', table_name='spam_verdicts')
    op.drop_column('spam_verdicts', 'art')
