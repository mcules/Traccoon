"""Model catalog: context window and approximate output speed

The catalog knew only prices. For the models behind one's own endpoint (LiteLLM and company)
the price is 0 though, and the choice is decided by something else there: how much context a
model carries and how fast it writes. Neither stood anywhere until now, so one had to know it
or try it out.

`context_tokens` is filled by the models.dev reconciliation for the cloud models; with local
ones it stays hand work, as does `speed_tps`: the speed hangs off the machine, not off the
model, and can only be measured.

Revision ID: a1d47f8c9b02
Revises: f2c8a91d40e5
Create Date: 2026-07-30
"""
from alembic import op
import sqlalchemy as sa


revision = 'a1d47f8c9b02'
down_revision = 'f2c8a91d40e5'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column('provider_models', sa.Column('context_tokens', sa.Integer(), nullable=True))
    op.add_column('provider_models', sa.Column('speed_tps', sa.Float(), nullable=True))


def downgrade() -> None:
    op.drop_column('provider_models', 'speed_tps')
    op.drop_column('provider_models', 'context_tokens')
