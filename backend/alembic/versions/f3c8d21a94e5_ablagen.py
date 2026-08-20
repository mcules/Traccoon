"""doc_series / doc_entries — Ablagen: Texte, die ein Ablauf schreibt

Ein Ablauf konnte seinen Text nirgends lassen: Der Rückblick, den ein Agent jeden Morgen
schreibt, landete im Ausgabefeld eines Job-Laufs (abgeschnitten bei 20.000 Zeichen), und die
Meldung dazu verwies auf `/digest/<Lauf>` — eine Seite, die es nie gab. Aufgebaut wie die
Messreihen: ein Name und eine Folge von Fassungen.

Revision ID: f3c8d21a94e5
Revises: e2a91c53f76b
"""
import sqlalchemy as sa
from alembic import op

revision = 'f3c8d21a94e5'
down_revision = 'e2a91c53f76b'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        'doc_series',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('owner_user_id', sa.Integer(),
                  sa.ForeignKey('users.id', ondelete='CASCADE'), nullable=True, index=True),
        sa.Column('key', sa.String(120), nullable=False),
        sa.Column('name', sa.String(200), server_default=''),
        sa.Column('description', sa.Text(), server_default=''),
        sa.Column('keep', sa.Integer(), server_default='60'),
        sa.Column('last_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('last_title', sa.String(300), server_default=''),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.UniqueConstraint('owner_user_id', 'key', name='uq_doc_series_owner_key'),
    )
    op.create_table(
        'doc_entries',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('series_id', sa.Integer(),
                  sa.ForeignKey('doc_series.id', ondelete='CASCADE'), index=True),
        sa.Column('ts', sa.DateTime(timezone=True), server_default=sa.func.now(), index=True),
        sa.Column('title', sa.String(300), server_default=''),
        sa.Column('body', sa.Text(), server_default=''),
        sa.Column('format', sa.String(20), server_default='markdown'),
        sa.Column('context', sa.JSON(), server_default='{}'),
    )


def downgrade() -> None:
    op.drop_table('doc_entries')
    op.drop_table('doc_series')
