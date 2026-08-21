"""Mail client: accounts and identities of a person

Revision ID: a9b4e73c1d20
Revises: f7c3d21a95e4
Create Date: 2026-08-19
"""
from alembic import op
import sqlalchemy as sa


revision = 'a9b4e73c1d20'
down_revision = 'f7c3d21a95e4'
branch_labels = None
depends_on = None


def upgrade() -> None:
    # The difference to imap-mcp: these accounts belong to a person, are maintained by them and
    # carry both ways — IMAP for reading, SMTP for sending.
    op.create_table(
        'mail_accounts',
        sa.Column('id', sa.Integer, primary_key=True),
        sa.Column('owner_user_id', sa.Integer,
                  sa.ForeignKey('users.id', ondelete='CASCADE'), nullable=True, index=True),
        sa.Column('name', sa.String(60), nullable=False),
        sa.Column('enabled', sa.Boolean, nullable=False, server_default=sa.true()),
        sa.Column('imap_host', sa.String(255), nullable=False, server_default=''),
        sa.Column('imap_port', sa.Integer, nullable=False, server_default='993'),
        sa.Column('imap_ssl', sa.Boolean, nullable=False, server_default=sa.true()),
        sa.Column('imap_user', sa.String(255), nullable=False, server_default=''),
        sa.Column('imap_password_enc', sa.Text, nullable=False, server_default=''),
        sa.Column('smtp_host', sa.String(255), nullable=False, server_default=''),
        sa.Column('smtp_port', sa.Integer, nullable=False, server_default='587'),
        sa.Column('smtp_security', sa.String(10), nullable=False, server_default='starttls'),
        sa.Column('smtp_user', sa.String(255), nullable=False, server_default=''),
        sa.Column('smtp_password_enc', sa.Text, nullable=False, server_default=''),
        sa.Column('auth_type', sa.String(20), nullable=False, server_default='password'),
        sa.Column('oauth_token_enc', sa.Text, nullable=False, server_default=''),
        sa.Column('folder_sent', sa.String(255), nullable=False, server_default='Sent'),
        sa.Column('folder_drafts', sa.String(255), nullable=False, server_default='Drafts'),
        sa.Column('folder_trash', sa.String(255), nullable=False, server_default='Trash'),
        sa.Column('folder_junk', sa.String(255), nullable=False, server_default='Junk'),
        sa.Column('folder_archive', sa.String(255), nullable=False, server_default='Archive'),
        sa.Column('archive_mode', sa.String(10), nullable=False, server_default='folder'),
        sa.Column('archive_pattern', sa.String(255), nullable=False,
                  server_default='Archive/{jahr}'),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.UniqueConstraint('owner_user_id', 'name', name='uq_mail_account_name'),
    )
    op.create_table(
        'mail_identities',
        sa.Column('id', sa.Integer, primary_key=True),
        sa.Column('account_id', sa.Integer,
                  sa.ForeignKey('mail_accounts.id', ondelete='CASCADE'), index=True),
        sa.Column('display_name', sa.String(120), nullable=False, server_default=''),
        sa.Column('email', sa.String(320), nullable=False),
        sa.Column('reply_to', sa.String(320), nullable=False, server_default=''),
        sa.Column('signature', sa.Text, nullable=False, server_default=''),
        sa.Column('is_default', sa.Boolean, nullable=False, server_default=sa.false()),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.func.now()),
    )


def downgrade() -> None:
    op.drop_table('mail_identities')
    op.drop_table('mail_accounts')
