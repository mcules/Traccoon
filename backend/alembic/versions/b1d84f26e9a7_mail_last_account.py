"""users.mail_last_account_id (the mailbox opened last)

Revision ID: b1d84f26e9a7
Revises: a9b4e73c1d20
Create Date: 2026-08-19
"""
from alembic import op
import sqlalchemy as sa


revision = 'b1d84f26e9a7'
down_revision = 'a9b4e73c1d20'
branch_labels = None
depends_on = None


def upgrade() -> None:
    # On the person and not in the browser: whoever logs in at the other machine wants to carry
    # on where they left off.
    op.add_column('users', sa.Column('mail_last_account_id', sa.Integer, nullable=True))
    op.create_foreign_key('fk_users_mail_last_account', 'users', 'mail_accounts',
                          ['mail_last_account_id'], ['id'], ondelete='SET NULL')
    upgrade_mcp()


def downgrade() -> None:
    op.drop_column('users', 'mail_mcp_token_enc')
    op.drop_column('mail_accounts', 'mcp_tools')
    op.drop_column('mail_accounts', 'mcp_ignore_folders')
    op.drop_column('mail_accounts', 'mcp_enabled')
    op.drop_constraint('fk_users_mail_last_account', 'users', type_='foreignkey')
    op.drop_column('users', 'mail_last_account_id')


# An addendum of the same round: Traccoon offers the mailboxes as MCP. Everything off until it
# jemand einschaltet — je Konto, je Werkzeug.
def upgrade_mcp() -> None:
    op.add_column('mail_accounts', sa.Column('mcp_enabled', sa.Boolean, nullable=False,
                                             server_default=sa.false()))
    op.add_column('mail_accounts', sa.Column('mcp_ignore_folders', sa.JSON, nullable=False,
                                             server_default='[]'))
    op.add_column('mail_accounts', sa.Column('mcp_tools', sa.JSON, nullable=False,
                                             server_default='[]'))
    op.add_column('users', sa.Column('mail_mcp_token_enc', sa.String, nullable=False,
                                     server_default=''))
