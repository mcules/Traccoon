"""projects testenv-Konfiguration

Revision ID: b7e2d5c81f04
Revises: a1c4f2e90b31
Create Date: 2026-07-19
"""
from alembic import op
import sqlalchemy as sa


revision = 'b7e2d5c81f04'
down_revision = 'a1c4f2e90b31'
branch_labels = None
depends_on = None

COLUMNS = [
    ('testenv_mode', sa.String(length=20), "'compose'"),
    ('testenv_container_port', sa.Integer(), '8080'),
    ('testenv_prestart', sa.Text(), "''"),
    ('testenv_env_enc', sa.Text(), "''"),
    ('testenv_demo_login', sa.Text(), "''"),
]


def upgrade() -> None:
    for name, type_, default in COLUMNS:
        op.add_column('projects', sa.Column(name, type_, nullable=False,
                                            server_default=sa.text(default)))


def downgrade() -> None:
    for name, _type, _default in COLUMNS:
        op.drop_column('projects', name)
