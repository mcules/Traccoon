"""Multi-Tenancy: Owner auf Jobs + Webhooks, Webhook-GUID

Revision ID: c3f8a1e40d92
Revises: b7e2d5c81f04
Create Date: 2026-07-19
"""
import uuid

from alembic import op
import sqlalchemy as sa


revision = 'c3f8a1e40d92'
down_revision = 'b7e2d5c81f04'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column('jobs', sa.Column('user_id', sa.Integer(), nullable=True))
    op.create_index('ix_jobs_user_id', 'jobs', ['user_id'])
    op.create_foreign_key('fk_jobs_user', 'jobs', 'users', ['user_id'], ['id'], ondelete='CASCADE')

    op.add_column('webhook_subs', sa.Column('public_id', sa.String(length=36), nullable=True))
    op.add_column('webhook_subs', sa.Column('owner_user_id', sa.Integer(), nullable=True))
    op.create_index('ix_webhook_subs_owner_user_id', 'webhook_subs', ['owner_user_id'])
    op.create_foreign_key('fk_webhook_owner', 'webhook_subs', 'users',
                          ['owner_user_id'], ['id'], ondelete='CASCADE')

    # Bestehende Webhooks bekommen eine GUID (Route war bisher der Schlüssel).
    conn = op.get_bind()
    for (wid,) in conn.execute(sa.text("SELECT id FROM webhook_subs WHERE public_id IS NULL")):
        conn.execute(sa.text("UPDATE webhook_subs SET public_id = :g WHERE id = :i"),
                     {"g": str(uuid.uuid4()), "i": wid})

    # Route war global unique — jetzt nur noch Label. Alten Unique-Index entfernen, public_id unique machen.
    op.drop_constraint('webhook_subs_route_key', 'webhook_subs', type_='unique')
    op.create_index('ix_webhook_subs_public_id', 'webhook_subs', ['public_id'], unique=True)


def downgrade() -> None:
    op.drop_index('ix_webhook_subs_public_id', 'webhook_subs')
    op.create_unique_constraint('webhook_subs_route_key', 'webhook_subs', ['route'])
    op.drop_constraint('fk_webhook_owner', 'webhook_subs', type_='foreignkey')
    op.drop_index('ix_webhook_subs_owner_user_id', 'webhook_subs')
    op.drop_column('webhook_subs', 'owner_user_id')
    op.drop_column('webhook_subs', 'public_id')
    op.drop_constraint('fk_jobs_user', 'jobs', type_='foreignkey')
    op.drop_index('ix_jobs_user_id', 'jobs')
    op.drop_column('jobs', 'user_id')
