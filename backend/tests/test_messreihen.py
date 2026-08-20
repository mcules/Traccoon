"""Metric series: drawing conclusions from numbers.

A flow used to see only the moment. "Battery 25 %" is meaningless on its own; only the series
says whether the device stops in two days or in two months. What is checked here is both:
that the line is right, and that the early warning comes exactly once (with a device that
reports daily, a daily warning would be the same warning as none).
"""
import datetime as dt

import pytest
from app.models.enums import WorkflowSubjectKind, WorkflowVersionStatus
from app.models.workflow import WorkflowDefinition, WorkflowInstance, WorkflowVersion
from app.services import metrics
from app.services.workflow_actions import run_action

from conftest import auth, make_user

pytestmark = pytest.mark.asyncio


def _tage(n: float) -> dt.datetime:
    return dt.datetime.now(tz=dt.timezone.utc) - dt.timedelta(days=n)


async def _akku_verlauf(db, owner, werte):
    """werte = [(days back, value)], the youngest one last."""
    for zurueck, wert in werte:
        await metrics.erfassen(db, owner.id, "akku.shelter", wert, einheit="%", ts=_tage(zurueck))
    await db.commit()
    return await metrics.reihe(db, owner.id, "akku.shelter")


async def test_gerade_trifft_den_echten_verlauf(db):
    """The real history of the tracker: 65 % on 27 July, 25 % on 18 August, so 1.8 %/day."""
    anna = await make_user(db, "anna")
    r = await _akku_verlauf(db, anna, [(22, 65), (18, 57), (14, 50), (10, 46), (6, 37),
                                       (2, 27), (0, 25)])
    stand = await metrics.trend(db, r, ziel=0.0)
    assert stand["points"] == 7
    assert -2.0 < stand["per_day"] < -1.6, stand
    assert 12 < stand["days_left"] < 16, stand      # rund zwei Wochen
    assert stand["fit"] > 0.97


async def test_zu_wenige_punkte_ergeben_keine_prognose(db):
    """Two measurements are not a series; better to say nothing than a random number."""
    anna = await make_user(db, "anna")
    r = await _akku_verlauf(db, anna, [(2, 50), (0, 40)])
    stand = await metrics.trend(db, r)
    assert stand["days_left"] is None and stand["per_day"] is None


async def test_steigende_reihe_hat_kein_ende(db):
    anna = await make_user(db, "anna")
    r = await _akku_verlauf(db, anna, [(4, 20), (3, 30), (2, 40), (0, 60)])
    stand = await metrics.trend(db, r, ziel=0.0)
    assert stand["per_day"] > 0 and stand["days_left"] is None


async def test_vorwarnung_kommt_genau_einmal(db):
    anna = await make_user(db, "anna")
    r = await _akku_verlauf(db, anna, [(22, 65), (14, 50), (6, 37), (0, 25)])
    stand = await metrics.trend(db, r, ziel=0.0)
    assert metrics.vorwarnen(r, stand["days_left"], 20) is True
    assert metrics.vorwarnen(r, stand["days_left"], 20) is False, "not the same thing twice"


async def test_neuer_akku_darf_wieder_warnen(db):
    anna = await make_user(db, "anna")
    r = await _akku_verlauf(db, anna, [(22, 65), (14, 50), (6, 37), (0, 25)])
    metrics.vorwarnen(r, 5.0, 7)
    assert r.warned_at is not None
    # A clear rise means refilled: the mark expires.
    await metrics.erfassen(db, anna.id, "akku.shelter", 100.0)
    await db.commit()
    assert r.warned_at is None
    assert metrics.vorwarnen(r, 5.0, 7) is True


async def _instanz(db, anna) -> WorkflowInstance:
    d = WorkflowDefinition(project_id=None, key="mess", name="Mess", created_by=anna.id,
                           subject_kind=WorkflowSubjectKind.standalone)
    db.add(d)
    await db.flush()
    v = WorkflowVersion(definition_id=d.id, version=1, graph={"nodes": [], "edges": []},
                        status=WorkflowVersionStatus.published)
    db.add(v)
    await db.flush()
    inst = WorkflowInstance(definition_id=d.id, version_id=v.id,
                            subject_kind=WorkflowSubjectKind.standalone,
                            context={"position": {"attributes": {"batteryLevel": 25}}},
                            started_by=anna.id)
    db.add(inst)
    await db.flush()
    return inst


async def test_aktion_schreibt_und_liest_ab(db):
    anna = await make_user(db, "anna")
    await _akku_verlauf(db, anna, [(22, 65), (14, 50), (6, 37)])
    inst = await _instanz(db, anna)
    node = {"id": "mess", "type": "auto_action", "data": {"config": {"action": {
        "action": "metric_record", "params": {
            "series": "akku.shelter", "value": "{{ position.attributes.batteryLevel }}",
            "unit": "%", "warn_days": 20}}}}}
    r = await run_action(db, inst, node)
    assert r["value"] == 25.0 and r["days_left"] and r["warn"] is True
    stand = inst.context["metric"]
    assert stand["unit"] == "%" and stand["empty_at"]


async def test_aktion_duldet_prozentzeichen_und_komma(db):
    anna = await make_user(db, "anna")
    inst = await _instanz(db, anna)
    node = {"id": "m", "type": "auto_action", "data": {"config": {"action": {
        "action": "metric_record", "params": {"series": "x", "value": "12,5 %"}}}}}
    assert (await run_action(db, inst, node))["value"] == 12.5


async def test_aktion_meckert_bei_unsinn(db):
    anna = await make_user(db, "anna")
    inst = await _instanz(db, anna)
    node = {"id": "m", "type": "auto_action", "data": {"config": {"action": {
        "action": "metric_record", "params": {"series": "x", "value": "keine Zahl"}}}}}
    with pytest.raises(ValueError):
        await run_action(db, inst, node)


async def test_unsinnige_werte_kommen_nicht_in_die_reihe(db):
    """The tracker reports `batteryLevel: 127` when it does not know the charge level.

    A single such point bends the line so that "empty in two weeks" becomes "rises slightly",
    and exactly that happened with the first real event.
    """
    anna = await make_user(db, "anna")
    await _akku_verlauf(db, anna, [(22, 65), (14, 50), (6, 37), (0, 25)])
    inst = await _instanz(db, anna)
    node = {"id": "m", "type": "auto_action", "data": {"config": {"action": {
        "action": "metric_record", "params": {"series": "akku.shelter", "value": 127,
                                         "min": 0, "max": 100}}}}}
    r = await run_action(db, inst, node)
    assert r["ignored"] is True
    r2 = await metrics.reihe(db, anna.id, "akku.shelter")
    assert r2.last_value == 25.0, "the last real value stays"
    stand = await metrics.trend(db, r2)
    assert stand["points"] == 4 and stand["per_day"] < 0

    # The run does not abort: the next branch should be able to recognise it.
    assert inst.context["metric"]["ignored"] is True
    assert inst.context["metric"]["warn"] is False


async def test_werte_aus_wenigen_minuten_ergeben_keinen_tagestrend(db):
    """Four voltage values out of three minutes gave +14 V per day: noise, extrapolated."""
    anna = await make_user(db, "anna")
    for minuten, wert in [(6, 3.50), (4, 3.50), (2, 3.50), (0, 3.52)]:
        await metrics.erfassen(db, anna.id, "spannung", wert, einheit="V",
                               ts=dt.datetime.now(tz=dt.timezone.utc) - dt.timedelta(minutes=minuten))
    await db.commit()
    r = await metrics.reihe(db, anna.id, "spannung")
    stand = await metrics.trend(db, r, ziel=3.2)
    assert stand["points"] == 4 and stand["per_day"] is None and stand["days_left"] is None


async def test_fehlender_wert_wird_uebersprungen(db):
    """An event without a measurement is not a defect.

    The tracker reports "I have dropped out" without a position, and therefore without a
    charge level. Before, the step failed and stood red in the log although nothing was broken.
    """
    anna = await make_user(db, "anna")
    await _akku_verlauf(db, anna, [(6, 40), (3, 34), (0, 30)])
    inst = await _instanz(db, anna)
    inst.context = {"event": {"type": "deviceInactive"}}      # no position in the payload
    node = {"id": "m", "type": "auto_action", "data": {"config": {"action": {
        "action": "metric_record", "params": {
            "series": "akku.shelter", "value": "{{ position.attributes.batteryLevel }}"}}}}}
    r = await run_action(db, inst, node)
    assert r["skipped"] is True and r["ignored"] is True
    assert inst.context["metric"]["warn"] is False
    # The last known state stays: with a dropout report it is the most interesting number
    # still available.
    assert inst.context["metric"]["value"] == 30.0
    reihe = await metrics.reihe(db, anna.id, "akku.shelter")
    assert reihe.last_value == 30.0
    assert (await metrics.trend(db, reihe))["points"] == 3, "no point was added"


async def test_pflichtwert_bleibt_ein_fehler(db):
    """Where the value is the purpose of the step, its absence should stand out."""
    anna = await make_user(db, "anna")
    inst = await _instanz(db, anna)
    node = {"id": "m", "type": "auto_action", "data": {"config": {"action": {
        "action": "metric_record", "params": {"series": "x", "value": "", "pflicht": True}}}}}
    with pytest.raises(ValueError):
        await run_action(db, inst, node)


# ── Silence: when nothing comes any more ─────────────────────────────────────

async def test_trend_nennt_das_alter_des_letzten_werts(db):
    anna = await make_user(db, "anna")
    r = await _akku_verlauf(db, anna, [(9, 60), (6, 50), (3, 40)])
    stand = await metrics.trend(db, r)
    assert 71 < stand["age_hours"] < 73        # drei Tage
    assert stand["last_at"]


async def test_alter_auch_ohne_gerade(db):
    """Exactly two values: too few for a forecast, enough for the age."""
    anna = await make_user(db, "anna")
    r = await _akku_verlauf(db, anna, [(5, 50), (4, 45)])
    stand = await metrics.trend(db, r)
    assert stand["per_day"] is None and stand["age_hours"] > 90


async def test_stille_wird_genau_einmal_gemeldet(db):
    anna = await make_user(db, "anna")
    r = await _akku_verlauf(db, anna, [(9, 60), (6, 50), (3, 40)])
    alter = (await metrics.trend(db, r))["age_hours"]
    assert metrics.stille_melden(r, alter, 26) is True
    assert metrics.stille_melden(r, alter, 26) is False, "an hourly watchdog must not be annoying"


async def test_neuer_wert_beendet_die_stille(db):
    anna = await make_user(db, "anna")
    r = await _akku_verlauf(db, anna, [(9, 60), (6, 50), (3, 40)])
    metrics.stille_melden(r, 72.0, 26)
    assert r.still_at is not None
    await metrics.erfassen(db, anna.id, "akku.shelter", 39.0)   # a bad value counts as well
    await db.commit()
    assert r.still_at is None
    assert metrics.stille_melden(r, 72.0, 26) is True


async def test_stille_und_restlaufzeit_verschlucken_sich_nicht(db):
    """Two facts, two marks; otherwise one message eats the other."""
    anna = await make_user(db, "anna")
    r = await _akku_verlauf(db, anna, [(22, 65), (14, 50), (6, 37), (3, 25)])
    stand = await metrics.trend(db, r, ziel=0.0)
    assert metrics.vorwarnen(r, stand["days_left"], 30) is True
    assert metrics.stille_melden(r, stand["age_hours"], 26) is True
    assert r.warned_at is not None and r.still_at is not None


async def test_aktion_liest_ohne_zu_schreiben(db):
    anna = await make_user(db, "anna")
    r = await _akku_verlauf(db, anna, [(9, 60), (6, 50), (3, 40)])
    inst = await _instanz(db, anna)
    node = {"id": "lesen", "type": "auto_action", "data": {"config": {"action": {
        "action": "metric_read",
        "params": {"series": "akku.shelter", "silence_hours": 26}}}}}
    erg = await run_action(db, inst, node)
    assert erg["silent"] is True and erg["report_silence"] is True
    assert (await metrics.trend(db, r))["points"] == 3, "no point may be created"
    stand = inst.context["metric"]
    assert stand["found"] is True and stand["value"] == 40.0


async def test_unbekannte_reihe_ist_kein_fehler(db):
    """A typo in the key would otherwise be a red run every hour."""
    anna = await make_user(db, "anna")
    inst = await _instanz(db, anna)
    node = {"id": "lesen", "type": "auto_action", "data": {"config": {"action": {
        "action": "metric_read", "params": {"series": "gibt.es.nicht",
                                                "silence_hours": 26}}}}}
    erg = await run_action(db, inst, node)
    assert erg["found"] is False
    assert inst.context["metric"]["report_silence"] is False


async def test_frische_reihe_schweigt_nicht(db):
    anna = await make_user(db, "anna")
    await _akku_verlauf(db, anna, [(0.2, 80), (0.1, 79), (0, 78)])
    inst = await _instanz(db, anna)
    node = {"id": "lesen", "type": "auto_action", "data": {"config": {"action": {
        "action": "metric_read", "params": {"series": "akku.shelter",
                                                "silence_hours": 26}}}}}
    erg = await run_action(db, inst, node)
    assert erg["silent"] is False and erg["report_silence"] is False


async def test_zahlen_duerfen_aus_dem_kontext_kommen(db):
    """The same watcher for several series: the threshold and the window come from the job.

    Before, the step failed on "{{ still_stunden }}", a text that was meant as a number.
    """
    anna = await make_user(db, "anna")
    await _akku_verlauf(db, anna, [(9, 60), (6, 50), (3, 40)])
    inst = await _instanz(db, anna)
    inst.context = {"series": "akku.shelter", "grenze": 26}
    node = {"id": "lesen", "type": "auto_action", "data": {"config": {"action": {
        "action": "metric_read",
        "params": {"series": "{{ series }}", "silence_hours": "{{ grenze }}"}}}}}
    erg = await run_action(db, inst, node)
    assert erg["silent"] is True and erg["report_silence"] is True


async def test_grenzen_duerfen_aus_dem_kontext_kommen(db):
    anna = await make_user(db, "anna")
    inst = await _instanz(db, anna)
    inst.context = {"obergrenze": 100, "roh": 127}
    node = {"id": "m", "type": "auto_action", "data": {"config": {"action": {
        "action": "metric_record", "params": {"series": "x", "value": "{{ roh }}",
                                         "min": 1, "max": "{{ obergrenze }}"}}}}}
    assert (await run_action(db, inst, node))["ignored"] is True


# ── The view: reading points and removing them one by one ───────────────────

async def test_punkte_kommen_mit_id_und_trend(client, db):
    anna = await make_user(db, "anna")
    await _akku_verlauf(db, anna, [(9, 60), (6, 50), (3, 40)])
    r = await client.get("/metrics/akku.shelter/points?days=30", headers=auth(anna))
    assert r.status_code == 200
    daten = r.json()
    assert [p["value"] for p in daten["points"]] == [60.0, 50.0, 40.0]
    assert all(p["id"] for p in daten["points"]), "deleting needs the id"
    assert daten["trend"]["per_day"] < 0


async def test_zeitraum_gilt_auch_fuer_die_gerade(client, db):
    """Shown and computed is the same window; otherwise the line does not fit the points."""
    anna = await make_user(db, "anna")
    await _akku_verlauf(db, anna, [(40, 100), (39, 99), (38, 98), (3, 40), (2, 38), (1, 36)])
    eng = (await client.get("/metrics/akku.shelter/points?days=7", headers=auth(anna))).json()
    weit = (await client.get("/metrics/akku.shelter/points?days=90", headers=auth(anna))).json()
    assert len(eng["points"]) == 3 and len(weit["points"]) == 6
    assert eng["trend"]["per_day"] != weit["trend"]["per_day"]


async def test_einzelnen_ausreisser_entfernen(client, db):
    """An outlier bends the line; without this path one would have to throw the series away."""
    anna = await make_user(db, "anna")
    await _akku_verlauf(db, anna, [(6, 60), (4, 50), (2, 40)])
    await metrics.erfassen(db, anna.id, "akku.shelter", 999.0)     # the outlier, last
    await db.commit()
    daten = (await client.get("/metrics/akku.shelter/points", headers=auth(anna))).json()
    schlecht = [p for p in daten["points"] if p["value"] == 999.0][0]

    weg = await client.delete(f"/metrics/akku.shelter/points/{schlecht['id']}",
                              headers=auth(anna))
    assert weg.status_code == 204
    r = await metrics.reihe(db, anna.id, "akku.shelter")
    await db.refresh(r)
    assert r.last_value == 40.0, "the head of the series moves up"
    danach = (await client.get("/metrics/akku.shelter/points", headers=auth(anna))).json()
    assert len(danach["points"]) == 3


async def test_fremder_wert_wird_nicht_geloescht(client, db):
    anna = await make_user(db, "anna")
    bert = await make_user(db, "bert")
    await metrics.erfassen(db, anna.id, "meins", 1.0)
    await metrics.erfassen(db, bert.id, "deins", 2.0)
    await db.commit()
    meins = (await client.get("/metrics/meins/points", headers=auth(anna))).json()
    pid = meins["points"][0]["id"]
    assert (await client.delete(f"/metrics/deins/points/{pid}",
                                headers=auth(bert))).status_code == 404


async def test_nachgetragener_altwert_veraendert_den_kopf_nicht(db):
    """The head points at the value that is last in time, not at the one entered last.

    Otherwise a series looks up to date after old values are added, and a state from the day
    before yesterday stood in the picture as "now".
    """
    anna = await make_user(db, "anna")
    await metrics.erfassen(db, anna.id, "akku.shelter", 42.0, einheit="%", ts=_tage(1))
    await metrics.erfassen(db, anna.id, "akku.shelter", 88.0, einheit="%", ts=_tage(3))
    await db.commit()
    r = await metrics.reihe(db, anna.id, "akku.shelter")
    assert r.last_value == 42.0
    # A really newer value on the other hand moves up.
    await metrics.erfassen(db, anna.id, "akku.shelter", 39.0, einheit="%")
    await db.commit()
    assert r.last_value == 39.0
