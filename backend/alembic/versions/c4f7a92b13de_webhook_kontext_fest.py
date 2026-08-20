"""webhook_subs.context_fixed — feste Kontextwerte am Auslöser

Zugleich Zusammenführung der beiden offenen Stränge (Mail-Client und Spam-Befunde), damit
`alembic upgrade head` wieder ein Ziel hat.

Der Kontext eines Laufs entsteht ab hier an einer Stelle: `context_map` holt aus der
Nutzlast, `context_fixed` setzt feste Werte (mit `{feld}` aus der Nutzlast). Die alten
Webhook-Modi `task`, `notify` und `assistant` sind damit entbehrlich — was sie taten, machen
Knoten im Ablauf. Bestehende Webhooks stellt `services/webhook_modes.umstellen()` beim
Start um; das braucht Graphen und steht deshalb nicht hier.

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
