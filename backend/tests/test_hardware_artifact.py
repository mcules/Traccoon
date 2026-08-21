"""Hardware is a real artifact: a common identity, one authoritative state.

The unit keeps its detail table (model, place, cost, warranty); identity, project and state
lie in `artifacts`, which processes and references point at as well.
"""
from app.models.artifact import Artifact
from app.models.enums import PurchaseStatus, WorkflowSubjectKind
from app.models.hardware import HardwareAsset
from app.services import artifacts as svc
from sqlalchemy import select
from conftest import auth, make_asset, make_project, make_user


async def test_an_item_gets_an_artifact_row(db):
    await svc.ensure_builtin_types(db)
    proj = await make_project(db, "TST", "Test")
    asset = await make_asset(db, "Switch", project=proj)

    a = await svc.ensure_for_asset(db, asset)
    await db.commit()
    assert asset.artifact_id == a.id
    assert a.project_id == proj.id
    assert a.status_key == asset.purchase_status.value
    kind = await svc.type_by_key(db, "hardware")
    assert a.type_id == kind.id
    assert "Switch" in a.title


async def test_the_state_reads_the_same_in_both_places(db):
    await svc.ensure_builtin_types(db)
    proj = await make_project(db, "TST", "Test")
    asset = await make_asset(db, "Router", project=proj)
    await svc.ensure_for_asset(db, asset)
    await db.commit()

    await svc.apply_status(db, subject_kind=WorkflowSubjectKind.hardware_asset, asset=asset,
                           status_key="installed")
    await db.commit()
    a = await db.get(Artifact, asset.artifact_id)
    assert asset.purchase_status == PurchaseStatus.installed
    assert a.status_key == "installed"          # authoritative, and congruent


async def test_backfilling_is_idempotent(db):
    await svc.ensure_builtin_types(db)
    proj = await make_project(db, "TST", "Test")
    await make_asset(db, "Switch", project=proj)
    await make_asset(db, "Router", project=proj)

    assert await svc.backfill_hardware_artifacts(db) == 2
    assert await svc.backfill_hardware_artifacts(db) == 0
    offen = (await db.execute(select(HardwareAsset).where(
        HardwareAsset.artifact_id.is_(None)))).scalars().all()
    assert offen == []


async def test_a_new_item_via_the_api_is_an_artifact_at_once(client, db):
    await svc.ensure_builtin_types(db)
    from app.models.enums import ProjectRole
    from app.models.hardware import HardwareModel
    from conftest import add_member
    owner = await make_user(db, "owner")
    proj = await make_project(db, "TST", "Test")
    await add_member(db, proj, owner, ProjectRole.owner)
    m = HardwareModel(name="Switch")
    db.add(m)
    await db.commit()

    r = await client.post("/hardware/assets", headers=auth(owner),
                          json={"model_id": m.id, "project_id": proj.id, "serial_number": "S-1"})
    assert r.status_code in (200, 201), r.text
    asset = await db.get(HardwareAsset, r.json()["id"])
    await db.refresh(asset)
    assert asset.artifact_id is not None
    a = await db.get(Artifact, asset.artifact_id)
    assert a.status_key == "planned" and "S-1" in a.title
