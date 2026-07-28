"""Artefakt-Register: Typen, Zustände und generische Artefakte

Ticket und Hardware bekommen eine gemeinsame, im Admin pflegbare Beschreibung; frei
definierte Typen speichern ihre Instanzen in `artifacts`.

Revision ID: c4e7b2a91f60
Revises: b3d5f81a20c7
Create Date: 2026-07-26
"""
from alembic import op
import sqlalchemy as sa


revision = 'c4e7b2a91f60'
down_revision = 'b3d5f81a20c7'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        'artifact_types',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column('key', sa.String(length=40), nullable=False),
        sa.Column('name', sa.String(length=100), nullable=False),
        sa.Column('plural', sa.String(length=100), nullable=False, server_default=''),
        sa.Column('icon', sa.String(length=16), nullable=False, server_default='📦'),
        sa.Column('color', sa.String(length=20), nullable=False, server_default='#58a6ff'),
        sa.Column('backing', sa.String(length=20), nullable=False, server_default='generic'),
        sa.Column('project_id', sa.Integer(), sa.ForeignKey('projects.id', ondelete='CASCADE'), nullable=True),
        sa.Column('builtin', sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column('enabled', sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column('description', sa.Text(), nullable=False, server_default=''),
        sa.Column('fields', sa.JSON(), nullable=False, server_default='[]'),
        sa.UniqueConstraint('key', name='uq_artifact_type_key'),
    )
    op.create_index('ix_artifact_types_key', 'artifact_types', ['key'])
    op.create_index('ix_artifact_types_project_id', 'artifact_types', ['project_id'])

    op.create_table(
        'artifact_statuses',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('type_id', sa.Integer(), sa.ForeignKey('artifact_types.id', ondelete='CASCADE'),
                  nullable=False),
        sa.Column('key', sa.String(length=40), nullable=False),
        sa.Column('label', sa.String(length=100), nullable=False),
        sa.Column('category', sa.String(length=20), nullable=False, server_default='in_progress'),
        sa.Column('order', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('is_default', sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column('waiting', sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.UniqueConstraint('type_id', 'key', name='uq_artifact_status'),
    )
    op.create_index('ix_artifact_statuses_type_id', 'artifact_statuses', ['type_id'])

    op.create_table(
        'artifacts',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column('type_id', sa.Integer(), sa.ForeignKey('artifact_types.id', ondelete='CASCADE'),
                  nullable=False),
        sa.Column('project_id', sa.Integer(), sa.ForeignKey('projects.id', ondelete='CASCADE'), nullable=True),
        sa.Column('title', sa.String(length=500), nullable=False),
        sa.Column('status_key', sa.String(length=40), nullable=False, server_default=''),
        sa.Column('data', sa.JSON(), nullable=False, server_default='{}'),
        sa.Column('created_by', sa.Integer(), sa.ForeignKey('users.id', ondelete='SET NULL'), nullable=True),
        sa.Column('closed_at', sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index('ix_artifacts_type_id', 'artifacts', ['type_id'])
    op.create_index('ix_artifacts_project_id', 'artifacts', ['project_id'])


def downgrade() -> None:
    op.drop_table('artifacts')
    op.drop_table('artifact_statuses')
    op.drop_table('artifact_types')
