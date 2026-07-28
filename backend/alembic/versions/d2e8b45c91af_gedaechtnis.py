"""Gedächtnis der Agenten im Obsidian-Vault (ABC-30)

Die Agenten konnten bisher nichts behalten: dauerhaft war nur die gelernte Mail-Regel
(`assistant_policies.action_hint`) und die gelernte Tool-Freigabe. Jede Vorgabe des Menschen
musste deshalb bei jedem Lauf neu gesagt werden.

Der Ablageort ist der Obsidian-Vault, weil der Mensch das Gelernte dort sehen und von Hand
korrigieren soll. Der Vault-Zugriff läuft über die MCP-Gruppe des Owners, das Gedächtnis ist
also zwingend persönlich — daher hängt der Ordner am Nutzer und nicht am Projekt.

`agent_definitions.learns` schaltet Abruf und Rückschau pro Agent. Standard ist an; ohne
gesetzten `users.vault_memory_path` passiert trotzdem nichts.

Revision ID: d2e8b45c91af
Revises: c1a7e94f5d32
Create Date: 2026-07-28
"""
from alembic import op
import sqlalchemy as sa


revision = 'd2e8b45c91af'
down_revision = 'c1a7e94f5d32'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("users", sa.Column("vault_memory_path", sa.String(500), nullable=False,
                                     server_default=""))
    op.add_column("agent_definitions", sa.Column("learns", sa.Boolean(), nullable=False,
                                                 server_default=sa.true()))


def downgrade() -> None:
    op.drop_column("agent_definitions", "learns")
    op.drop_column("users", "vault_memory_path")
