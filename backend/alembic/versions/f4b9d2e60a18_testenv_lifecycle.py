"""Testumgebungs-Lebenszyklus: Projekt-Settings + branch_testenvs (ABC-18)

Revision ID: f4b9d2e60a18
Revises: e2c8b5d31f47
Create Date: 2026-07-25
"""
from alembic import op
import sqlalchemy as sa


revision = 'f4b9d2e60a18'
down_revision = 'e2c8b5d31f47'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column('projects', sa.Column(
        'testenv_enabled', sa.Boolean(), nullable=False, server_default=sa.true()))
    op.add_column('projects', sa.Column(
        'testenv_compose_file', sa.String(length=255), nullable=False,
        server_default='compose.preview.yml'))
    op.add_column('projects', sa.Column(
        'testenv_dockerfile', sa.String(length=255), nullable=False, server_default='Dockerfile'))
    op.add_column('projects', sa.Column(
        'testenv_url_template', sa.String(length=255), nullable=False,
        server_default='http://{host}:{port}'))

    op.create_table(
        'branch_testenvs',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column('project_id', sa.Integer(), sa.ForeignKey('projects.id', ondelete='CASCADE'),
                  nullable=False, index=True),
        sa.Column('branch', sa.String(length=255), nullable=False),
        sa.Column('status', sa.String(length=20), nullable=False, server_default='starting'),
        sa.Column('url', sa.String(length=1000), nullable=True),
        sa.Column('container', sa.String(length=200), nullable=True),
        sa.Column('port', sa.Integer(), nullable=True),
        sa.Column('error', sa.Text(), nullable=True),
        sa.Column('created_by', sa.Integer(), sa.ForeignKey('users.id', ondelete='SET NULL'),
                  nullable=True),
        sa.Column('started_at', sa.DateTime(timezone=True), nullable=True),
    )

    # „Testen"-Spalte für Bestandsprojekte anlegen und vor „Fertig" einsortieren.
    op.execute("""
        INSERT INTO workflow_statuses (project_id, name, category, "order")
        SELECT p.id, 'Testen', 'in_progress',
               COALESCE((SELECT MIN(s."order") FROM workflow_statuses s
                         WHERE s.project_id = p.id AND s.category = 'done'), 99)
        FROM projects p
        WHERE NOT EXISTS (SELECT 1 FROM workflow_statuses s
                          WHERE s.project_id = p.id AND s.name = 'Testen')
    """)
    # „Fertig" hinter „Testen" schieben — absolut statt inkrementell, damit ein
    # wiederholter Lauf die Reihenfolge nicht weiter aufbläht.
    op.execute("""
        UPDATE workflow_statuses d SET "order" = t."order" + 1
        FROM workflow_statuses t
        WHERE t.project_id = d.project_id AND t.name = 'Testen'
          AND d.category = 'done' AND d."order" <= t."order"
    """)
    op.execute("""
        INSERT INTO board_columns (board_id, status_id, "order")
        SELECT b.id, s.id, s."order"
        FROM workflow_statuses s
        JOIN boards b ON b.project_id = s.project_id
        WHERE s.name = 'Testen'
          AND NOT EXISTS (SELECT 1 FROM board_columns c
                          WHERE c.board_id = b.id AND c.status_id = s.id)
    """)
    # Spaltenreihenfolge am Status ausrichten (sonst kollidiert „Testen" mit „Fertig").
    op.execute("""
        UPDATE board_columns c SET "order" = s."order"
        FROM workflow_statuses s
        WHERE s.id = c.status_id AND c."order" <> s."order"
    """)


def downgrade() -> None:
    op.drop_table('branch_testenvs')
    op.drop_column('projects', 'testenv_url_template')
    op.drop_column('projects', 'testenv_dockerfile')
    op.drop_column('projects', 'testenv_compose_file')
    op.drop_column('projects', 'testenv_enabled')
