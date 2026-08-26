"""A name per project on the membership.

Empty and NOT NULL rather than nullable: there is no third state between "set" and "not
set", and every read would otherwise have to know about NULL as well as about "".

Revision ID: b7d1e4f92a03
Revises: f2c48d9a6b31
"""
from alembic import op
import sqlalchemy as sa

revision = "b7d1e4f92a03"
down_revision = "f2c48d9a6b31"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("project_members",
                  sa.Column("alias", sa.String(length=255), nullable=False, server_default=""))


def downgrade() -> None:
    op.drop_column("project_members", "alias")
