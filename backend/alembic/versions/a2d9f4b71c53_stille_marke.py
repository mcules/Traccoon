"""A silence mark on the metric series

The forecast says when something comes to an end. It does not say that nothing comes any
more, and exactly that is the more dangerous case: if the counterpart drops out, it no longer
reports its own failure either, and silence looks like a quiet day. The mark makes sure that
the falling silent is reported exactly once, not hourly.

Revision ID: a2d9f4b71c53
Revises: f1c8a52d6e07
Create Date: 2026-08-18
"""
import sqlalchemy as sa
from alembic import op

revision = 'a2d9f4b71c53'
down_revision = 'f1c8a52d6e07'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("metric_series", sa.Column("still_at", sa.DateTime(timezone=True),
                                             nullable=True))


def downgrade() -> None:
    op.drop_column("metric_series", "still_at")
