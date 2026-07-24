"""users.email optional (login-lose Konten)

Revision ID: e8a4c1f60b73
Revises: d7b3e1c50a92
Create Date: 2026-07-24
"""
from alembic import op
import sqlalchemy as sa


revision = 'e8a4c1f60b73'
down_revision = 'd7b3e1c50a92'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.alter_column('users', 'email', existing_type=sa.String(length=255), nullable=True)


def downgrade() -> None:
    # Vor dem Wiederherstellen von NOT NULL müssten NULL-E-Mails aufgefüllt werden.
    op.alter_column('users', 'email', existing_type=sa.String(length=255), nullable=False)
