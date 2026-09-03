"""a note area per person

Everything the note workspace keeps on the account, and the calendars it reads.
Until now these existed only through the additive statements the backend runs at
start, which is enough for a database that has been running along but not for
one built from the migrations — there the note area simply had no columns.

Revision ID: eb0c798a133d
Revises: e6a92d41f708
Create Date: 2026-09-03
"""
from alembic import op
import sqlalchemy as sa


revision = 'eb0c798a133d'
down_revision = 'e6a92d41f708'
branch_labels = None
depends_on = None

# One vault per person, and how their note area behaves. The preferences are one
# field each rather than a column per setting: they are view settings, and the
# next one must not cost a migration.
COLUMNS = (
    ('vault_path', sa.String(500), "''"),
    ('notes_prefs', sa.JSON, "'{}'::json"),
    ('notes_ui_state', sa.JSON, "'{}'::json"),
    # The account an appointment is written back through. A share link is public
    # and read only, so creating one needs a login.
    ('notes_caldav_url', sa.String(500), "''"),
    ('notes_caldav_user', sa.String(255), "''"),
    ('notes_caldav_password_enc', sa.Text, "''"),
)


def upgrade() -> None:
    for name, kind, default in COLUMNS:
        op.add_column('users', sa.Column(name, kind, nullable=False,
                                         server_default=sa.text(default)))

    op.create_table(
        'notes_calendars',
        sa.Column('id', sa.Integer, primary_key=True),
        sa.Column('owner_user_id', sa.Integer,
                  sa.ForeignKey('users.id', ondelete='CASCADE'), nullable=False,
                  index=True),
        sa.Column('name', sa.String(120), nullable=False, server_default=''),
        sa.Column('url', sa.String(1000), nullable=False, server_default=''),
        sa.Column('link_target', sa.String(500), nullable=False, server_default=''),
        sa.Column('auth_user', sa.String(255), nullable=False, server_default=''),
        # Fernet. The password never leaves the server and is never sent back
        # out; the interface only learns whether one is set — which is why it is
        # a column of its own and not a key in a JSON blob nobody would protect.
        sa.Column('auth_password_enc', sa.Text, nullable=False, server_default=''),
        sa.Column('position', sa.Integer, nullable=False, server_default='0'),
        # Off keeps the entry but stops it being fetched, which is what somebody
        # wants for a calendar that is temporarily noisy.
        sa.Column('enabled', sa.Boolean, nullable=False, server_default=sa.true()),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.func.now()),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.func.now()),
    )


def downgrade() -> None:
    op.drop_table('notes_calendars')
    for name, _kind, _default in reversed(COLUMNS):
        op.drop_column('users', name)
