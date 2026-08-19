"""Multi-tenancy: an owner on jobs and webhooks, a webhook GUID

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

    # Existing webhooks get a GUID (the route used to be the key).
    conn = op.get_bind()
    for (wid,) in conn.execute(sa.text("SELECT id FROM webhook_subs WHERE public_id IS NULL")):
        conn.execute(sa.text("UPDATE webhook_subs SET public_id = :g WHERE id = :i"),
                     {"g": str(uuid.uuid4()), "i": wid})

    # The route was globally unique and is only a label now. Remove the old unique index and make public_id unique.
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
