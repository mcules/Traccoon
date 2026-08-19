"""Memory of the agents in the Obsidian vault (ABC-30)

The agents could keep nothing until now: permanent were only the learned mail rule
(`assistant_policies.action_hint`) and the learned tool approval. Every rule of the human
therefore had to be said again on every run.

The filing place is the Obsidian vault, because the human should see what has been learned
there and correct it by hand. Vault access runs over the MCP group of the owner, so the
memory is necessarily personal, and the folder hangs off the user, not the project.

`agent_definitions.learns` switches fetching and review per agent. The default is on.

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
