"""Modellkatalog: Kontextfenster und ungefähre Ausgabegeschwindigkeit

Der Katalog kannte nur Preise. Für die Modelle hinter dem eigenen Endpoint (LiteLLM & Co.)
ist der Preis aber 0 — die Wahl entscheidet sich dort an etwas anderem: wie viel Kontext ein
Modell trägt und wie schnell es schreibt. Beides stand bisher nirgends, also musste man es
wissen oder ausprobieren.

`context_tokens` füllt der models.dev-Abgleich für die Cloud-Modelle mit; bei lokalen bleibt
es Handarbeit, ebenso `speed_tps` — die Geschwindigkeit hängt an der Maschine, nicht am
Modell, und lässt sich nur messen.

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
