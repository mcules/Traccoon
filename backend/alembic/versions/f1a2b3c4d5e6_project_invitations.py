"""project_invitations — Einladung per E-Mail-Adresse

Revision ID: f1a2b3c4d5e6
Revises: e4f9c2a81b73
Create Date: 2026-07-21
"""
import sqlalchemy as sa
from alembic import op


revision = 'f1a2b3c4d5e6'
down_revision = 'e4f9c2a81b73'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        'project_invitations',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('project_id', sa.Integer(),
                  sa.ForeignKey('projects.id', ondelete='CASCADE'), nullable=False, index=True),
        sa.Column('email', sa.String(length=255), nullable=False, index=True),
        sa.Column('role', sa.Enum('owner', 'maintainer', 'member', 'viewer', name='projectrole'),
                  nullable=False, server_default='member'),
        sa.Column('token', sa.String(length=64), nullable=False, unique=True, index=True),
        sa.Column('invited_by_user_id', sa.Integer(),
                  sa.ForeignKey('users.id', ondelete='SET NULL'), nullable=True),
        sa.Column('status', sa.String(length=20), nullable=False, server_default='pending'),
        sa.Column('accepted_user_id', sa.Integer(),
                  sa.ForeignKey('users.id', ondelete='SET NULL'), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column('expires_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('accepted_at', sa.DateTime(timezone=True), nullable=True),
    )


def downgrade() -> None:
    op.drop_table('project_invitations')
