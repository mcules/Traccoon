"""Stille-Marke an der Messreihe

Die Prognose sagt, wann etwas zu Ende geht. Sie sagt nicht, dass gar nichts mehr kommt —
und genau das ist der gefährlichere Fall: fällt die Gegenstelle aus, meldet sie auch ihre
eigene Störung nicht mehr, und Stille sieht aus wie ein ruhiger Tag. Die Marke sorgt
dafür, dass das Verstummen genau einmal gemeldet wird, nicht stündlich.

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
