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


def _days(n: float) -> dt.datetime:
    return dt.datetime.now(tz=dt.timezone.utc) - dt.timedelta(days=n)


async def _akku_verlauf(db, owner, values):
    """werte = [(days back, value)], the youngest one last."""
    for zurueck, value in values:
        await metrics.record(db, owner.id, "akku.shelter", value, unit="%", ts=_days(zurueck))
    await db.commit()
    return await metrics.reihe(db, owner.id, "akku.shelter")


async def test_the_line_fit_matches_the_real_history(db):
    """The real history of the tracker: 65 % on 27 July, 25 % on 18 August, so 1.8 %/day."""
    anna = await make_user(db, "anna")
    r = await _akku_verlauf(db, anna, [(22, 65), (18, 57), (14, 50), (10, 46), (6, 37),
                                       (2, 27), (0, 25)])
    state = await metrics.trend(db, r, target=0.0)
    assert state["points"] == 7
    assert -2.0 < state["per_day"] < -1.6, state
    assert 12 < state["days_left"] < 16, state      # rund zwei Wochen
    assert state["fit"] > 0.97


async def test_too_few_points_yield_no_forecast(db):
    """Two measurements are not a series; better to say nothing than a random number."""
    anna = await make_user(db, "anna")
    r = await _akku_verlauf(db, anna, [(2, 50), (0, 40)])
    state = await metrics.trend(db, r)
    assert state["days_left"] is None and state["per_day"] is None


async def test_a_rising_series_has_no_end(db):
    anna = await make_user(db, "anna")
    r = await _akku_verlauf(db, anna, [(4, 20), (3, 30), (2, 40), (0, 60)])
    state = await metrics.trend(db, r, target=0.0)
    assert state["per_day"] > 0 and state["days_left"] is None


async def test_the_early_warning_comes_exactly_once(db):
    anna = await make_user(db, "anna")
    r = await _akku_verlauf(db, anna, [(22, 65), (14, 50), (6, 37), (0, 25)])
    state = await metrics.trend(db, r, target=0.0)
    assert metrics.vorwarnen(r, state["days_left"], 20) is True
    assert metrics.vorwarnen(r, state["days_left"], 20) is False, "not the same thing twice"


async def test_a_fresh_battery_may_warn_again(db):
    anna = await make_user(db, "anna")
    r = await _akku_verlauf(db, anna, [(22, 65), (14, 50), (6, 37), (0, 25)])
    metrics.vorwarnen(r, 5.0, 7)
    assert r.warned_at is not None
    # A clear rise means refilled: the mark expires.
    await metrics.record(db, anna.id, "akku.shelter", 100.0)
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


async def test_the_action_writes_and_reads_back(db):
    anna = await make_user(db, "anna")
    await _akku_verlauf(db, anna, [(22, 65), (14, 50), (6, 37)])
    inst = await _instanz(db, anna)
    node = {"id": "mess", "type": "auto_action", "data": {"config": {"action": {
        "action": "metric_record", "params": {
            "series": "akku.shelter", "value": "{{ position.attributes.batteryLevel }}",
            "unit": "%", "warn_days": 20}}}}}
    r = await run_action(db, inst, node)
    assert r["value"] == 25.0 and r["days_left"] and r["warn"] is True
    state = inst.context["metric"]
    assert state["unit"] == "%" and state["empty_at"]


async def test_the_action_tolerates_a_percent_sign_and_a_comma(db):
    anna = await make_user(db, "anna")
    inst = await _instanz(db, anna)
    node = {"id": "m", "type": "auto_action", "data": {"config": {"action": {
        "action": "metric_record", "params": {"series": "x", "value": "12,5 %"}}}}}
    assert (await run_action(db, inst, node))["value"] == 12.5


async def test_the_action_complains_about_nonsense(db):
    anna = await make_user(db, "anna")
    inst = await _instanz(db, anna)
    node = {"id": "m", "type": "auto_action", "data": {"config": {"action": {
        "action": "metric_record", "params": {"series": "x", "value": "keine Zahl"}}}}}
    with pytest.raises(ValueError):
        await run_action(db, inst, node)


async def test_nonsensical_values_do_not_enter_the_series(db):
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
    state = await metrics.trend(db, r2)
    assert state["points"] == 4 and state["per_day"] < 0

    # The run does not abort: the next branch should be able to recognise it.
    assert inst.context["metric"]["ignored"] is True
    assert inst.context["metric"]["warn"] is False


async def test_values_from_a_few_minutes_yield_no_daily_trend(db):
    """Four voltage values out of three minutes gave +14 V per day: noise, extrapolated."""
    anna = await make_user(db, "anna")
    for minutes, value in [(6, 3.50), (4, 3.50), (2, 3.50), (0, 3.52)]:
        await metrics.record(db, anna.id, "spannung", value, unit="V",
                               ts=dt.datetime.now(tz=dt.timezone.utc) - dt.timedelta(minutes=minutes))
    await db.commit()
    r = await metrics.reihe(db, anna.id, "spannung")
    state = await metrics.trend(db, r, target=3.2)
    assert state["points"] == 4 and state["per_day"] is None and state["days_left"] is None


async def test_a_missing_value_is_skipped(db):
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


async def test_a_required_value_stays_an_error(db):
    """Where the value is the purpose of the step, its absence should stand out."""
    anna = await make_user(db, "anna")
    inst = await _instanz(db, anna)
    node = {"id": "m", "type": "auto_action", "data": {"config": {"action": {
        "action": "metric_record", "params": {"series": "x", "value": "", "pflicht": True}}}}}
    with pytest.raises(ValueError):
        await run_action(db, inst, node)


# ── Silence: when nothing comes any more ─────────────────────────────────────

async def test_the_trend_names_the_age_of_the_last_value(db):
    anna = await make_user(db, "anna")
    r = await _akku_verlauf(db, anna, [(9, 60), (6, 50), (3, 40)])
    state = await metrics.trend(db, r)
    assert 71 < state["age_hours"] < 73        # drei Tage
    assert state["last_at"]


async def test_the_age_even_without_a_line_fit(db):
    """Exactly two values: too few for a forecast, enough for the age."""
    anna = await make_user(db, "anna")
    r = await _akku_verlauf(db, anna, [(5, 50), (4, 45)])
    state = await metrics.trend(db, r)
    assert state["per_day"] is None and state["age_hours"] > 90


async def test_silence_is_reported_exactly_once(db):
    anna = await make_user(db, "anna")
    r = await _akku_verlauf(db, anna, [(9, 60), (6, 50), (3, 40)])
    alter = (await metrics.trend(db, r))["age_hours"]
    assert metrics.silence_report(r, alter, 26) is True
    assert metrics.silence_report(r, alter, 26) is False, "an hourly watchdog must not be annoying"


async def test_a_new_value_ends_the_silence(db):
    anna = await make_user(db, "anna")
    r = await _akku_verlauf(db, anna, [(9, 60), (6, 50), (3, 40)])
    metrics.silence_report(r, 72.0, 26)
    assert r.still_at is not None
    await metrics.record(db, anna.id, "akku.shelter", 39.0)   # a bad value counts as well
    await db.commit()
    assert r.still_at is None
    assert metrics.silence_report(r, 72.0, 26) is True


async def test_silence_and_remaining_runtime_do_not_swallow_each_other(db):
    """Two facts, two marks; otherwise one message eats the other."""
    anna = await make_user(db, "anna")
    r = await _akku_verlauf(db, anna, [(22, 65), (14, 50), (6, 37), (3, 25)])
    state = await metrics.trend(db, r, target=0.0)
    assert metrics.vorwarnen(r, state["days_left"], 30) is True
    assert metrics.silence_report(r, state["age_hours"], 26) is True
    assert r.warned_at is not None and r.still_at is not None


async def test_the_action_reads_without_writing(db):
    anna = await make_user(db, "anna")
    r = await _akku_verlauf(db, anna, [(9, 60), (6, 50), (3, 40)])
    inst = await _instanz(db, anna)
    node = {"id": "lesen", "type": "auto_action", "data": {"config": {"action": {
        "action": "metric_read",
        "params": {"series": "akku.shelter", "silence_hours": 26}}}}}
    erg = await run_action(db, inst, node)
    assert erg["silent"] is True and erg["report_silence"] is True
    assert (await metrics.trend(db, r))["points"] == 3, "no point may be created"
    state = inst.context["metric"]
    assert state["found"] is True and state["value"] == 40.0


async def test_an_unknown_series_is_not_an_error(db):
    """A typo in the key would otherwise be a red run every hour."""
    anna = await make_user(db, "anna")
    inst = await _instanz(db, anna)
    node = {"id": "lesen", "type": "auto_action", "data": {"config": {"action": {
        "action": "metric_read", "params": {"series": "gibt.es.nicht",
                                                "silence_hours": 26}}}}}
    erg = await run_action(db, inst, node)
    assert erg["found"] is False
    assert inst.context["metric"]["report_silence"] is False


async def test_a_fresh_series_does_not_stay_quiet(db):
    anna = await make_user(db, "anna")
    await _akku_verlauf(db, anna, [(0.2, 80), (0.1, 79), (0, 78)])
    inst = await _instanz(db, anna)
    node = {"id": "lesen", "type": "auto_action", "data": {"config": {"action": {
        "action": "metric_read", "params": {"series": "akku.shelter",
                                                "silence_hours": 26}}}}}
    erg = await run_action(db, inst, node)
    assert erg["silent"] is False and erg["report_silence"] is False


async def test_numbers_may_come_from_the_context(db):
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


async def test_limits_may_come_from_the_context(db):
    anna = await make_user(db, "anna")
    inst = await _instanz(db, anna)
    inst.context = {"obergrenze": 100, "roh": 127}
    node = {"id": "m", "type": "auto_action", "data": {"config": {"action": {
        "action": "metric_record", "params": {"series": "x", "value": "{{ roh }}",
                                         "min": 1, "max": "{{ obergrenze }}"}}}}}
    assert (await run_action(db, inst, node))["ignored"] is True


# ── The view: reading points and removing them one by one ───────────────────

async def test_points_come_with_an_id_and_a_trend(client, db):
    anna = await make_user(db, "anna")
    await _akku_verlauf(db, anna, [(9, 60), (6, 50), (3, 40)])
    r = await client.get("/metrics/akku.shelter/points?days=30", headers=auth(anna))
    assert r.status_code == 200
    daten = r.json()
    assert [p["value"] for p in daten["points"]] == [60.0, 50.0, 40.0]
    assert all(p["id"] for p in daten["points"]), "deleting needs the id"
    assert daten["trend"]["per_day"] < 0


async def test_the_span_also_applies_to_the_line_fit(client, db):
    """Shown and computed is the same window; otherwise the line does not fit the points."""
    anna = await make_user(db, "anna")
    await _akku_verlauf(db, anna, [(40, 100), (39, 99), (38, 98), (3, 40), (2, 38), (1, 36)])
    eng = (await client.get("/metrics/akku.shelter/points?days=7", headers=auth(anna))).json()
    weit = (await client.get("/metrics/akku.shelter/points?days=90", headers=auth(anna))).json()
    assert len(eng["points"]) == 3 and len(weit["points"]) == 6
    assert eng["trend"]["per_day"] != weit["trend"]["per_day"]


async def test_removing_a_single_outlier(client, db):
    """An outlier bends the line; without this path one would have to throw the series away."""
    anna = await make_user(db, "anna")
    await _akku_verlauf(db, anna, [(6, 60), (4, 50), (2, 40)])
    await metrics.record(db, anna.id, "akku.shelter", 999.0)     # the outlier, last
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


async def test_a_foreign_value_is_not_deleted(client, db):
    anna = await make_user(db, "anna")
    bert = await make_user(db, "bert")
    await metrics.record(db, anna.id, "meins", 1.0)
    await metrics.record(db, bert.id, "deins", 2.0)
    await db.commit()
    meins = (await client.get("/metrics/meins/points", headers=auth(anna))).json()
    pid = meins["points"][0]["id"]
    assert (await client.delete(f"/metrics/deins/points/{pid}",
                                headers=auth(bert))).status_code == 404


async def test_a_backfilled_old_value_does_not_change_the_header(db):
    """The head points at the value that is last in time, not at the one entered last.

    Otherwise a series looks up to date after old values are added, and a state from the day
    before yesterday stood in the picture as "now".
    """
    anna = await make_user(db, "anna")
    await metrics.record(db, anna.id, "akku.shelter", 42.0, unit="%", ts=_days(1))
    await metrics.record(db, anna.id, "akku.shelter", 88.0, unit="%", ts=_days(3))
    await db.commit()
    r = await metrics.reihe(db, anna.id, "akku.shelter")
    assert r.last_value == 42.0
    # A really newer value on the other hand moves up.
    await metrics.record(db, anna.id, "akku.shelter", 39.0, unit="%")
    await db.commit()
    assert r.last_value == 39.0
