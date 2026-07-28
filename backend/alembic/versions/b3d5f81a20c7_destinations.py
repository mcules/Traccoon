"""Ziele: benannte externe Gegenstellen mit Basis-URL + Authentifizierung

Ein Ziel bündelt Basis-URL und Anmeldung (Basic, Bearer, API-Key, HMAC, OAuth2 Client
Credentials). Aufrufe aus Prozessen, Jobs und dem Agenten-Werkzeug nennen nur den Namen.

Revision ID: b3d5f81a20c7
Revises: a1c7e94f30b2
Create Date: 2026-07-26
"""
from alembic import op
import sqlalchemy as sa


revision = 'b3d5f81a20c7'
down_revision = 'a1c7e94f30b2'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        'destinations',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column('name', sa.String(length=80), nullable=False),
        sa.Column('label', sa.String(length=200), nullable=False, server_default=''),
        sa.Column('description', sa.Text(), nullable=False, server_default=''),
        sa.Column('user_id', sa.Integer(), sa.ForeignKey('users.id', ondelete='CASCADE'), nullable=True),
        sa.Column('project_id', sa.Integer(), sa.ForeignKey('projects.id', ondelete='CASCADE'), nullable=True),
        sa.Column('base_url', sa.String(length=1000), nullable=False),
        sa.Column('auth_type', sa.String(length=20), nullable=False, server_default='none'),
        sa.Column('username', sa.String(length=200), nullable=False, server_default=''),
        sa.Column('secret_enc', sa.Text(), nullable=False, server_default=''),
        sa.Column('api_key_name', sa.String(length=120), nullable=False, server_default='X-API-Key'),
        sa.Column('api_key_in', sa.String(length=10), nullable=False, server_default='header'),
        sa.Column('hmac_header', sa.String(length=120), nullable=False,
                  server_default='X-Webhook-Signature'),
        sa.Column('hmac_algo', sa.String(length=20), nullable=False, server_default='sha256'),
        sa.Column('hmac_prefix', sa.String(length=20), nullable=False, server_default=''),
        sa.Column('oauth_token_url', sa.String(length=1000), nullable=False, server_default=''),
        sa.Column('oauth_client_id', sa.String(length=300), nullable=False, server_default=''),
        sa.Column('oauth_scope', sa.String(length=500), nullable=False, server_default=''),
        sa.Column('oauth_audience', sa.String(length=500), nullable=False, server_default=''),
        sa.Column('oauth_token_enc', sa.Text(), nullable=False, server_default=''),
        sa.Column('oauth_expires_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('default_headers', sa.JSON(), nullable=False, server_default='{}'),
        sa.Column('timeout_sec', sa.Integer(), nullable=False, server_default='30'),
        sa.Column('verify_tls', sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column('enabled', sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column('allow_agents', sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column('created_by', sa.Integer(), sa.ForeignKey('users.id', ondelete='SET NULL'), nullable=True),
        sa.Column('last_used_at', sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index('ix_destinations_name', 'destinations', ['name'])
    op.create_index('ix_destinations_user_id', 'destinations', ['user_id'])
    op.create_index('ix_destinations_project_id', 'destinations', ['project_id'])
    # Ein Name je Geltungsbereich (NULL-Spalten gelten als verschieden → partielle Indizes).
    op.create_index('uq_destination_global', 'destinations', ['name'], unique=True,
                    postgresql_where=sa.text('user_id IS NULL AND project_id IS NULL'))
    op.create_index('uq_destination_user', 'destinations', ['user_id', 'name'], unique=True,
                    postgresql_where=sa.text('user_id IS NOT NULL AND project_id IS NULL'))
    op.create_index('uq_destination_project', 'destinations', ['project_id', 'name'], unique=True,
                    postgresql_where=sa.text('project_id IS NOT NULL'))

    # Job-Art „http": Ziel + Aufrufdaten nach Zeitplan.
    op.add_column('jobs', sa.Column('destination_id', sa.Integer(),
                                    sa.ForeignKey('destinations.id', ondelete='SET NULL'),
                                    nullable=True))
    op.add_column('jobs', sa.Column('http_request', sa.JSON(), nullable=False, server_default='{}'))


def downgrade() -> None:
    op.drop_column('jobs', 'http_request')
    op.drop_column('jobs', 'destination_id')
    op.drop_table('destinations')
