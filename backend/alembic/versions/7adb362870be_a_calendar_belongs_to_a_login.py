"""a calendar belongs to a login, and there is more than one of those

Writing an appointment needs a login. Until now there was exactly one, on the
person — one server, one account. That does not survive contact with reality:
appointments live on several servers, and one server holds several accounts when
somebody has a private and a work login on the same machine.

So the login becomes a thing of its own and a calendar names the one it belongs
to. A calendar without one is a subscription: readable, and nothing more.

What stood on the person moves into a login of its own here. The old columns are
left in place rather than dropped — they are what this reads from, and taking
them away in the same step would leave nothing to fall back to if this is ever
rolled back.

Revision ID: 7adb362870be
Revises: 36f10191080a
Create Date: 2026-09-03
"""
from alembic import op
import sqlalchemy as sa


revision = '7adb362870be'
down_revision = '36f10191080a'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        'notes_calendar_servers',
        sa.Column('id', sa.Integer, primary_key=True),
        sa.Column('owner_user_id', sa.Integer,
                  sa.ForeignKey('users.id', ondelete='CASCADE'), nullable=False, index=True),
        # Two logins to the same machine are told apart by this and by nothing
        # else, so it is the one field that carries the difference.
        sa.Column('label', sa.String(120), nullable=False, server_default=''),
        sa.Column('url', sa.String(500), nullable=False, server_default=''),
        sa.Column('username', sa.String(255), nullable=False, server_default=''),
        # Fernet. It never leaves the server and never comes back out.
        sa.Column('password_enc', sa.Text, nullable=False, server_default=''),
        sa.Column('position', sa.Integer, nullable=False, server_default='0'),
        sa.Column('enabled', sa.Boolean, nullable=False, server_default=sa.true()),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.func.now()),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.func.now()),
    )

    op.add_column('notes_calendars', sa.Column(
        'server_id', sa.Integer,
        sa.ForeignKey('notes_calendar_servers.id', ondelete='SET NULL'), nullable=True))
    op.create_index('ix_notes_calendars_server_id', 'notes_calendars', ['server_id'])
    # Which collection on that server this is — the server's own name for it.
    op.add_column('notes_calendars', sa.Column(
        'caldav_id', sa.String(255), nullable=False, server_default=''))

    op.execute("""
        INSERT INTO notes_calendar_servers
            (owner_user_id, label, url, username, password_enc, position, enabled,
             created_at, updated_at)
        SELECT id, 'CalDAV', notes_caldav_url, notes_caldav_user,
               notes_caldav_password_enc, 0, true, now(), now()
        FROM users
        WHERE notes_caldav_url <> ''
    """)


def downgrade() -> None:
    op.drop_index('ix_notes_calendars_server_id', table_name='notes_calendars')
    op.drop_column('notes_calendars', 'caldav_id')
    op.drop_column('notes_calendars', 'server_id')
    op.drop_table('notes_calendar_servers')
