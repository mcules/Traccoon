"""Hardware is a real artifact: a common identity, one authoritative state.

The unit keeps its detail table (model, place, cost, warranty); identity, project and state
lie in `artifacts`, which processes and references point at as well.
"""
from app.models.artifact import Artifact
from app.models.enums import PurchaseStatus, WorkflowSubjectKind
from app.models.hardware import HardwareAsset
from app.services import artifacts as art
from sqlalchemy import select
from conftest import auth, make_asset, make_project, make_user


async def test_exemplar_bekommt_eine_artefakt_zeile(db):
    await art.ensure_builtin_types(db)
    proj = await make_project(db, "TST", "Test")
    asset = await make_asset(db, "Switch", project=proj)

    a = await art.ensure_for_asset(db, asset)
    await db.commit()
    assert asset.artifact_id == a.id
    assert a.project_id == proj.id
    assert a.status_key == asset.purchase_status.value
    typ = await art.type_by_key(db, "hardware")
    assert a.type_id == typ.id
    assert "Switch" in a.title


async def test_zustand_steht_an_beiden_stellen_gleich(db):
    await art.ensure_builtin_types(db)
    proj = await make_project(db, "TST", "Test")
    asset = await make_asset(db, "Router", project=proj)
    await art.ensure_for_asset(db, asset)
    await db.commit()

    await art.apply_status(db, subject_kind=WorkflowSubjectKind.hardware_asset, asset=asset,
                           status_key="installed")
    await db.commit()
    a = await db.get(Artifact, asset.artifact_id)
    assert asset.purchase_status == PurchaseStatus.installed
    assert a.status_key == "installed"          # authoritative, and congruent


async def test_nachtragen_ist_idempotent(db):
    await art.ensure_builtin_types(db)
    proj = await make_project(db, "TST", "Test")
    await make_asset(db, "Switch", project=proj)
    await make_asset(db, "Router", project=proj)

    assert await art.backfill_hardware_artifacts(db) == 2
    assert await art.backfill_hardware_artifacts(db) == 0
    offen = (await db.execute(select(HardwareAsset).where(
        HardwareAsset.artifact_id.is_(None)))).scalars().all()
    assert offen == []


async def test_neues_exemplar_ueber_die_api_ist_sofort_artefakt(client, db):
    await art.ensure_builtin_types(db)
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
