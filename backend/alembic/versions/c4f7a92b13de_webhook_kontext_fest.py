"""webhook_subs.context_fixed — feste Kontextwerte am Auslöser

At the same time a merge of the two open strands (mail client and spam findings), so that
`alembic upgrade head` wieder ein Ziel hat.

From here on the context of a run comes into being in one place: `context_map` fetches from the
payload, `context_fixed` sets fixed values (with `{field}` from the payload). The old webhook
modes `task`, `notify` and `assistant` are thereby dispensable — what they did is done by nodes
in the flow. Existing webhooks are converted at startup by `services/webhook_modes.convert()`;
that needs graphs and therefore does not stand here.

Revision ID: c4f7a92b13de
Revises: b1d84f26e9a7, c7f2a41d95b3
"""
import sqlalchemy as sa
from alembic import op

revision = 'c4f7a92b13de'
down_revision = ('b1d84f26e9a7', 'c7f2a41d95b3')
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column('webhook_subs', sa.Column(
        'context_fixed', sa.JSON(), nullable=False, server_default=sa.text("'{}'::jsonb")))


def downgrade() -> None:
    op.drop_column('webhook_subs', 'context_fixed')
