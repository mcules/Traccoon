"""Hardware wird ein Artefakt: gemeinsame Identität + Zustand in `artifacts`

Das Exemplar behält seine Detailtabelle (Modell, Ort, Kosten, Garantie); Identität,
Projekt und Zustand liegen künftig in `artifacts`, worauf auch Prozesse zeigen. Die alte
Spalte `purchase_status` wird vorerst mitgeschrieben, damit Oberfläche und Filter weiter
laufen — sie entfällt in einer späteren Etappe.

Revision ID: d5a1c93e7b28
Revises: c4e7b2a91f60
Create Date: 2026-07-26
"""
from alembic import op
import sqlalchemy as sa


revision = 'd5a1c93e7b28'
down_revision = 'c4e7b2a91f60'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column('hardware_assets', sa.Column(
        'artifact_id', sa.Integer(), sa.ForeignKey('artifacts.id', ondelete='CASCADE'),
        nullable=True))
    op.create_index('uq_hardware_asset_artifact', 'hardware_assets', ['artifact_id'],
                    unique=True, postgresql_where=sa.text('artifact_id IS NOT NULL'))
    op.add_column('workflow_instances', sa.Column(
        'artifact_id', sa.Integer(), sa.ForeignKey('artifacts.id', ondelete='SET NULL'),
        nullable=True))
    op.create_index('ix_workflow_instances_artifact', 'workflow_instances', ['artifact_id'])

    # Artefakt-Zeile je Exemplar anlegen und verknüpfen. Titel = Modell · Seriennummer.
    op.execute("""
        INSERT INTO artifacts (type_id, project_id, title, status_key, data, created_at, updated_at)
        SELECT t.id, h.project_id,
               trim(both ' ·' from concat_ws(' · ', m.name, h.serial_number)),
               h.purchase_status::text, '{}'::json, now(), now()
        FROM hardware_assets h
        JOIN artifact_types t ON t.key = 'hardware'
        LEFT JOIN hardware_models m ON m.id = h.model_id
        WHERE h.artifact_id IS NULL
    """)
    op.execute("""
        UPDATE hardware_assets h SET artifact_id = a.id
        FROM artifacts a
        JOIN artifact_types t ON t.id = a.type_id AND t.key = 'hardware'
        WHERE h.artifact_id IS NULL
          AND a.title = trim(both ' ·' from concat_ws(' · ',
                (SELECT m.name FROM hardware_models m WHERE m.id = h.model_id), h.serial_number))
          AND a.status_key = h.purchase_status::text
          AND a.project_id IS NOT DISTINCT FROM h.project_id
    """)
    # Laufende Beschaffungs-Prozesse auf das Artefakt umhängen.
    op.execute("""
        UPDATE workflow_instances w SET artifact_id = h.artifact_id
        FROM hardware_assets h
        WHERE w.hardware_asset_id = h.id AND w.artifact_id IS NULL
    """)


def downgrade() -> None:
    op.drop_index('ix_workflow_instances_artifact', table_name='workflow_instances')
    op.drop_column('workflow_instances', 'artifact_id')
    op.drop_index('uq_hardware_asset_artifact', table_name='hardware_assets')
    op.drop_column('hardware_assets', 'artifact_id')
    op.execute("DELETE FROM artifacts WHERE type_id IN "
               "(SELECT id FROM artifact_types WHERE key = 'hardware')")
