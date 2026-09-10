"""Which tool groups an agent carries from the first turn, and how long it may run

Everything an agent is allowed used to be in the prompt before it read the task.
Measured on 2026-09-09: 293 tool schemas, 75k tokens of context before the first
word of work, and 141 of those schemas belonged to a game while the task was
about notes. Every turn paid for all of it.

So the arrangement of the skills moves to the tools: what is named here is in the
prompt, everything else `allowed_tools` permits stands in a catalogue and is
fetched with `load_tools` when the task turns out to need it.

Empty for everyone at the start — that is the point, not an oversight. An agent
that names nothing here fetches every group on demand, and the runs say soon
enough which group is worth carrying along.

Beside it a second column of the same kind: how long one run of this agent may
take. It was an environment variable for every agent alike, so a development
session and a mail sorter were bound by the same half hour. 0 keeps the house
default, which is what everything that exists starts with.

Revision ID: c4e18b7f9a02
Revises: 9b1e4c7a25d3
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = 'c4e18b7f9a02'
down_revision = '9b1e4c7a25d3'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("agent_definitions", sa.Column(
        "autoload_tools", sa.JSON(), nullable=False, server_default=sa.text("'[]'")))
    op.add_column("agent_definitions", sa.Column(
        "max_run_seconds", sa.Integer(), nullable=False, server_default="0"))


def downgrade() -> None:
    op.drop_column("agent_definitions", "max_run_seconds")
    op.drop_column("agent_definitions", "autoload_tools")
