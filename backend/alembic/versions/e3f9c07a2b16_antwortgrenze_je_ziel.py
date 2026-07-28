"""Antwortgrenze je Ziel statt einer pauschalen Obergrenze (TRA-31)

`MAX_RESPONSE_CHARS = 4000` galt für jedes Ziel gleichermaßen. Für Gegenstellen, die ihre
Lage bewusst in EINEM Abruf liefern, ist das zu wenig: die UniWar-Bot-API antwortet mit rund
12 000 Zeichen, ein Agent plante damit auf abgeschnittenem JSON — schlimmer als gar keine
Antwort, weil der Schnitt nicht auffällt.

Die Grenze wandert deshalb an das Ziel. Der Standard bleibt 4000, sodass sich für bestehende
Ziele nichts ändert; nur wer es ausdrücklich braucht, hebt sie an.

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
