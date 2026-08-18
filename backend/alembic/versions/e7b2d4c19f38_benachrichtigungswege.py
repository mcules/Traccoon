"""Benachrichtigungswege gehören zur Person

Bisher gab es genau einen Weg: die Glocke, und — falls eine Chat-ID hinterlegt war —
Telegram. Wer eine Benachrichtigung auslöst, weiß aber selten, ob der Empfänger Telegram
überhaupt benutzt. Deshalb entscheidet die Person, auf welchem Weg sie erreicht wird;
der Absender darf einen Weg vorgeben, muss aber nicht.

Revision ID: e7b2d4c19f38
Revises: d5a1c7f38e62
Create Date: 2026-08-18
"""
import sqlalchemy as sa
from alembic import op

revision = 'e7b2d4c19f38'
down_revision = 'd5a1c7f38e62'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("users", sa.Column("notify_default", sa.String(20),
                                     nullable=False, server_default="telegram"))
    op.add_column("users", sa.Column("notify_email", sa.String(255), nullable=True))
    # Wer keine Chat-ID hat, wurde bisher ohnehin nur über die Glocke erreicht — für den
    # ist E-Mail der ehrlichere Standard, sofern eine Adresse hinterlegt ist.
    op.execute("""
        UPDATE users SET notify_default = 'email'
         WHERE (telegram_chat_id IS NULL OR telegram_chat_id = '')
           AND email IS NOT NULL AND email <> ''
    """)


def downgrade() -> None:
    op.drop_column("users", "notify_email")
    op.drop_column("users", "notify_default")
