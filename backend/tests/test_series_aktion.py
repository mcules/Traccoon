"""Die Ablauf-Aktion `series_record` — eine für alle Arten.

Es gibt eine Sorte Reihe mit einer Art daran, also auch genau eine Aktion, die hineinschreibt.
Was sich je Art unterscheidet, sind die Felder — nicht der Vorgang. Geprüft wird deshalb vor
allem, dass die Art aus der Reihe kommt und nicht aus dem Parameter: Sonst könnte ein Ablauf
die Art einer bestehenden Reihe unter sich wegziehen.
"""
import datetime as dt

from sqlalchemy import select

from app.models.enums import WorkflowSubjectKind, WorkflowVersionStatus
from app.models.series import Series
from app.models.workflow import WorkflowDefinition, WorkflowInstance, WorkflowVersion
from app.services.workflow_actions import run_action
from conftest import make_user


def _node(params: dict) -> dict:
    return {"id": "s", "type": "auto_action",
            "data": {"config": {"action": {"action": "series_record", "params": params}}}}


async def _lauf(db, user) -> WorkflowInstance:
    d = WorkflowDefinition(project_id=None, key="reihen", name="Reihen", created_by=user.id,
                           subject_kind=WorkflowSubjectKind.standalone)
    db.add(d)
    await db.flush()
    v = WorkflowVersion(definition_id=d.id, version=1, graph={"nodes": [], "edges": []},
                        status=WorkflowVersionStatus.published)
    db.add(v)
    await db.flush()
    inst = WorkflowInstance(definition_id=d.id, version_id=v.id,
                            subject_kind=WorkflowSubjectKind.standalone,
                            context={}, started_by=user.id)
    db.add(inst)
    await db.flush()
    return inst


async def _series(db, key) -> Series:
    return (await db.execute(select(Series).where(Series.key == key))).scalar_one_or_none()


async def test_zahl_write_legt_die_series_an(db):
    user = await make_user(db, "chef")
    inst = await _lauf(db, user)

    aus = await run_action(db, inst, _node({
        "series": "akku.shelter", "kind": "number", "value": "25", "name": "Akku Shelter"}))
    await db.commit()

    assert aus["stored"] is True and aus["kind"] == "number"
    reihe = await _series(db, "akku.shelter")
    assert reihe.kind == "number" and reihe.points == 1
    assert reihe.state["value"] == 25.0
    assert inst.context["series"]["value"] == 25.0


async def test_location_write_mit_geozaun(db):
    from app.models.series import SeriesPlace
    user = await make_user(db, "chef")
    inst = await _lauf(db, user)
    db.add(SeriesPlace(owner_user_id=user.id, key="zuhause", name="Zuhause",
                       lat=50.0825, lon=10.5663, radius_m=150))
    await db.flush()

    aus = await run_action(db, inst, _node({
        "series": "tracker.shelter", "kind": "location",
        "lat": "50.0825", "lon": "10.5663", "battery": "25"}))
    await db.commit()

    assert aus["stored"] is True and aus["entered"] == ["zuhause"]
    assert aus["lat"] == 50.0825 and aus["battery"] == 25.0
    assert (await _series(db, "tracker.shelter")).kind == "location"


async def test_text_write_nimmt_die_first_line_as_title(db):
    user = await make_user(db, "chef")
    inst = await _lauf(db, user)

    aus = await run_action(db, inst, _node({
        "series": "news.ki", "kind": "text", "body": "# Was heute war\n\nEiniges."}))
    await db.commit()

    assert aus["stored"] is True and aus["kind"] == "text"
    assert (await _series(db, "news.ki")).state["title"] == "Was heute war"


async def test_die_kind_kommt_aus_der_series_nicht_aus_dem_parameter(db):
    """Sonst zoege ein Ablauf einer bestehenden Reihe die Art unter den Fuessen weg."""
    user = await make_user(db, "chef")
    inst = await _lauf(db, user)
    await run_action(db, inst, _node({"series": "a.b", "kind": "number", "value": "1"}))
    await db.flush()

    aus = await run_action(db, inst, _node({
        "series": "a.b", "kind": "location", "lat": "50", "lon": "10"}))
    await db.commit()

    assert aus["kind"] == "number"
    # Ohne Zahl im Parameter gibt es nichts zu schreiben — aber die Reihe bleibt, was sie ist.
    assert aus["stored"] is False
    assert (await _series(db, "a.b")).kind == "number"


async def test_fehlender_value_ist_kein_error(db):
    """Ein Geraet meldet auch seinen Zustand, wenn es einen Wert gerade nicht kennt."""
    user = await make_user(db, "chef")
    inst = await _lauf(db, user)

    aus = await run_action(db, inst, _node({
        "series": "akku.shelter", "kind": "number", "value": "{{ gibt.es.nicht }}"}))
    await db.commit()
    assert aus["stored"] is False and aus["skipped"] is True
    # Ohne Wert entsteht auch keine leere Reihe.
    assert await _series(db, "akku.shelter") is None


async def test_pflicht_laesst_es_krachen(db):
    user = await make_user(db, "chef")
    inst = await _lauf(db, user)
    try:
        await run_action(db, inst, _node({
            "series": "akku.shelter", "kind": "number", "value": "", "required": True}))
    except ValueError as exc:
        assert "kein Wert" in str(exc)
    else:
        raise AssertionError("haette krachen muessen")


async def test_grenzen_werfen_ausreisser_weg(db):
    user = await make_user(db, "chef")
    inst = await _lauf(db, user)
    db.add(Series(owner_user_id=user.id, key="akku.x", kind="number",
                  settings={"min": 1, "max": 100}))
    await db.flush()

    aus = await run_action(db, inst, _node({"series": "akku.x", "value": "8200"}))
    await db.commit()
    assert aus["stored"] is False and aus["skipped"] == 1
    assert (await _series(db, "akku.x")).points == 0


async def test_deutsche_parameter_gehen_mit(db):
    """`reihe`, `art`, `wert` bildet workflow_terms ohnehin ab."""
    user = await make_user(db, "chef")
    inst = await _lauf(db, user)

    aus = await run_action(db, inst, _node({"reihe": "temp.buero", "art": "number",
                                              "wert": "21.5", "quelle": "traccar"}))
    await db.commit()
    assert aus["stored"] is True and aus["value"] == 21.5


async def test_text_series_raeumt_nach_keep_auf(db):
    user = await make_user(db, "chef")
    inst = await _lauf(db, user)
    db.add(Series(owner_user_id=user.id, key="news.kurz", kind="text",
                  settings={"keep": 2}))
    await db.flush()

    for i in range(4):
        await run_action(db, inst, _node({"series": "news.kurz", "body": f"Fassung {i}"}))
    await db.commit()

    from app.models.series import SeriesPoint
    points = (await db.execute(select(SeriesPoint).where(
        SeriesPoint.series_id == (await _series(db, "news.kurz")).id))).scalars().all()
    assert len(points) == 2
    assert {p.body for p in points} == {"Fassung 2", "Fassung 3"}
