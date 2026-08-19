"""A response limit per destination instead of a flat upper bound (TRA-31)

`MAX_RESPONSE_CHARS = 4000` applied to every destination alike. For counterparts that
deliberately deliver their state in ONE call that is too little: the UniWar bot API answers
with around 12 000 characters, and an agent planned on truncated JSON with that, which is
worse than no answer, because the cut does not stand out.

The limit therefore moves to the destination. The default stays 4000, so nothing changes for
existing destinations; only whoever explicitly needs it raises it.

Revision ID: e3f9c07a2b16
Revises: d2e8b45c91af
Create Date: 2026-07-28
"""
from alembic import op
import sqlalchemy as sa


revision = 'e3f9c07a2b16'
down_revision = 'd2e8b45c91af'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("destinations", sa.Column("max_response_chars", sa.Integer(), nullable=False,
                                            server_default="4000"))


def downgrade() -> None:
    op.drop_column("destinations", "max_response_chars")
