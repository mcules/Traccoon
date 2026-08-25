"""mail_documents (which attachment became which document)

Revision ID: f2c48d9a6b31
Revises: e5a72c9d18b4
Create Date: 2026-08-25
"""
from alembic import op
import sqlalchemy as sa


revision = 'f2c48d9a6b31'
down_revision = 'e5a72c9d18b4'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        'mail_documents',
        sa.Column('id', sa.Integer, primary_key=True),
        sa.Column('account_id', sa.Integer,
                  sa.ForeignKey('mail_accounts.id', ondelete='CASCADE'), nullable=False,
                  index=True),
        sa.Column('folder', sa.String(255), nullable=False),
        sa.Column('uid', sa.Integer, nullable=False),
        sa.Column('attachment', sa.Integer, nullable=False, server_default='-1'),
        sa.Column('filename', sa.String(500), nullable=False, server_default=''),
        sa.Column('system', sa.String(40), nullable=False, server_default='paperless'),
        sa.Column('doc_id', sa.String(80), nullable=False, server_default=''),
        sa.Column('doc_url', sa.String(1000), nullable=False, server_default=''),
        sa.Column('title', sa.String(500), nullable=False, server_default=''),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.func.now()),
        # One document per attachment: the second report about the same file is a repetition,
        # not a second document.
        sa.UniqueConstraint('account_id', 'folder', 'uid', 'attachment',
                            name='uq_mail_document'),
    )


def downgrade() -> None:
    op.drop_table('mail_documents')
