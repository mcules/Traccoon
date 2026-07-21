"""Issue: ondelete=CASCADE fuer type_id/status_id/reporter_id (NOT NULL, Projekt-Loeschen)

Revision ID: a9f4c2b81e07
Revises: f9a3c1e57d24
Create Date: 2026-07-21
"""
from alembic import op


revision = 'a9f4c2b81e07'
down_revision = 'a2b8d3f04c19'
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Alle drei Spalten sind NOT NULL -> SET NULL wuerde Nullable-Semantik brechen,
    # daher CASCADE (fachlich korrekt: Issue kann ohne gueltigen Typ/Status/Reporter nicht existieren).
    op.drop_constraint('issues_type_id_fkey', 'issues', type_='foreignkey')
    op.create_foreign_key(None, 'issues', 'issue_types', ['type_id'], ['id'], ondelete='CASCADE')

    op.drop_constraint('issues_status_id_fkey', 'issues', type_='foreignkey')
    op.create_foreign_key(None, 'issues', 'workflow_statuses', ['status_id'], ['id'], ondelete='CASCADE')

    op.drop_constraint('issues_reporter_id_fkey', 'issues', type_='foreignkey')
    op.create_foreign_key(None, 'issues', 'users', ['reporter_id'], ['id'], ondelete='CASCADE')


def downgrade() -> None:
    op.drop_constraint('issues_reporter_id_fkey', 'issues', type_='foreignkey')
    op.create_foreign_key(None, 'issues', 'users', ['reporter_id'], ['id'])

    op.drop_constraint('issues_status_id_fkey', 'issues', type_='foreignkey')
    op.create_foreign_key(None, 'issues', 'workflow_statuses', ['status_id'], ['id'])

    op.drop_constraint('issues_type_id_fkey', 'issues', type_='foreignkey')
    op.create_foreign_key(None, 'issues', 'issue_types', ['type_id'], ['id'])
