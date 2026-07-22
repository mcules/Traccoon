"""Sub-Projekte: inherit_members-Schalter, Location.project_id, resource_grants

Revision ID: a3d7c9b12f56
Revises: e4f9c2a81b73
Create Date: 2026-07-22
"""
from alembic import op
import sqlalchemy as sa


revision = 'a3d7c9b12f56'
down_revision = 'e4f9c2a81b73'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        'projects',
        sa.Column('inherit_members', sa.Boolean(), nullable=False, server_default=sa.true()),
    )
    op.add_column(
        'locations',
        sa.Column('project_id', sa.Integer(), nullable=True),
    )
    op.create_index('ix_locations_project_id', 'locations', ['project_id'])
    op.create_foreign_key(
        'fk_locations_project', 'locations', 'projects', ['project_id'], ['id'], ondelete='SET NULL',
    )

    resourcetype = sa.Enum('location', 'asset', name='resourcetype')
    grantlevel = sa.Enum('view', 'manage', name='grantlevel')
    resourcetype.create(op.get_bind(), checkfirst=True)
    grantlevel.create(op.get_bind(), checkfirst=True)

    op.create_table(
        'resource_grants',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column('project_id', sa.Integer(), sa.ForeignKey('projects.id', ondelete='CASCADE'), nullable=False, index=True),
        sa.Column('user_id', sa.Integer(), sa.ForeignKey('users.id', ondelete='CASCADE'), nullable=False, index=True),
        sa.Column('resource_type', resourcetype, nullable=False),
        sa.Column('resource_id', sa.Integer(), nullable=False),
        sa.Column('level', grantlevel, nullable=False, server_default='view'),
        sa.Column('recursive', sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.UniqueConstraint('user_id', 'resource_type', 'resource_id', name='uq_resource_grant'),
    )


def downgrade() -> None:
    op.drop_table('resource_grants')
    grantlevel = sa.Enum('view', 'manage', name='grantlevel')
    resourcetype = sa.Enum('location', 'asset', name='resourcetype')
    grantlevel.drop(op.get_bind(), checkfirst=True)
    resourcetype.drop(op.get_bind(), checkfirst=True)

    op.drop_constraint('fk_locations_project', 'locations', type_='foreignkey')
    op.drop_index('ix_locations_project_id', table_name='locations')
    op.drop_column('locations', 'project_id')
    op.drop_column('projects', 'inherit_members')
