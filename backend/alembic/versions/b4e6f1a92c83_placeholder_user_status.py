"""UserStatus.placeholder: placeholder accounts for person assignment without a login

Revision ID: b4e6f1a92c83
Revises: a3b1c9d72f40
Create Date: 2026-07-22
"""
import sqlalchemy as sa
from alembic import op

revision = 'b4e6f1a92c83'
down_revision = 'a3b1c9d72f40'
branch_labels = None
depends_on = None


def upgrade() -> None:
    # A new enum value for accounts that exist only as an assignment target (no login).
    op.execute("ALTER TYPE userstatus ADD VALUE IF NOT EXISTS 'placeholder'")


def downgrade() -> None:
    # Postgres cannot remove enum values, so the downgrade is a no-op (for documentation).
    pass
