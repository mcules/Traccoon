"""Reading a data series from a flow: where it stands, where it heads, whether it went quiet.

The new series could be written from a flow and not read, so every watchdog still hung off
the old metric series and both models stayed alive side by side. What this answers follows
`metric_read` field for field, so a flow moving over changes its action and nothing else.
"""
import datetime as dt

from app.models.enums import WorkflowSubjectKind, WorkflowVersionStatus
from app.models.series import Series, SeriesPoint
from app.models.workflow import WorkflowDefinition, WorkflowInstance, WorkflowVersion
from app.services.workflow_actions import run_action
from conftest import make_user
from sqlalchemy import select


def _node(params: dict) -> dict:
    return {"id": "r", "type": "auto_action",
            "data": {"config": {"action": {"action": "series_read", "params": params}}}}


async def _instance(db, owner) -> WorkflowInstance:
    definition = WorkflowDefinition(project_id=None, key="watch", name="Wache",
                                    created_by=owner.id,
                                    subject_kind=WorkflowSubjectKind.standalone)
    db.add(definition)
    await db.flush()
    version = WorkflowVersion(definition_id=definition.id, version=1,
                              graph={"nodes": [], "edges": []},
                              status=WorkflowVersionStatus.published)
    db.add(version)
    await db.flush()
    inst = WorkflowInstance(definition_id=definition.id, version_id=version.id,
                            subject_kind=WorkflowSubjectKind.standalone,
                            context={}, started_by=owner.id)
    db.add(inst)
    await db.flush()
    return inst


async def _falling(db, owner, key="akku.shelter", days=10, start=100.0, step=-5.0):
    """A series that loses `step` per day, the shape a battery has."""
    row = Series(owner_user_id=owner.id, key=key, kind="number", name=key,
                 settings={"unit": "%"})
    db.add(row)
    await db.flush()
    now = dt.datetime.now(tz=dt.timezone.utc)
    value = start
    for d in range(days, -1, -1):
        db.add(SeriesPoint(series_id=row.id, ts=now - dt.timedelta(days=d), value=value))
        value += step
    row.points = days + 1
    row.state = {"value": value - step}
    row.last_at = now
    await db.commit()
    return row


async def test_an_unknown_series_is_not_an_error(db):
    """A typo in the key would otherwise be a red run every hour."""
    user = await make_user(db, "waechter")
    inst = await _instance(db, user)

    out = await run_action(db, inst, _node({"series": "gibt.es.nicht"}))

    assert out["found"] is False
    assert inst.context["series"]["found"] is False


async def test_it_reads_the_state_and_the_direction(db):
    user = await make_user(db, "waechter")
    await _falling(db, user)
    inst = await _instance(db, user)

    out = await run_action(db, inst, _node({"series": "akku.shelter", "target": 0}))

    # `found` stands in the not-found branch only, exactly as `metric_read` has it.
    state = inst.context["series"]
    assert out["kind"] == "number" and state["found"] is True
    assert state["value"] == 50.0 and state["unit"] == "%"
    # Five per day downwards, fifty left: ten days to nothing.
    assert round(state["per_day"]) == -5
    assert 9 <= state["days_left"] <= 11
    assert state["empty_at"] is not None


async def test_a_series_moving_away_gets_no_forecast(db):
    """No number is more honest than one invented out of a rising line."""
    user = await make_user(db, "waechter")
    await _falling(db, user, key="akku.steigt", start=20.0, step=5.0)
    inst = await _instance(db, user)

    await run_action(db, inst, _node({"series": "akku.steigt", "target": 0}))

    assert inst.context["series"]["days_left"] is None


async def test_too_few_points_yield_no_line(db):
    user = await make_user(db, "waechter")
    row = Series(owner_user_id=user.id, key="akku.jung", kind="number", name="jung")
    db.add(row)
    await db.flush()
    now = dt.datetime.now(tz=dt.timezone.utc)
    db.add(SeriesPoint(series_id=row.id, ts=now, value=42.0))
    row.state = {"value": 42.0}
    row.last_at = now
    await db.commit()
    inst = await _instance(db, user)

    await run_action(db, inst, _node({"series": "akku.jung"}))

    state = inst.context["series"]
    assert state["value"] == 42.0
    assert state["per_day"] is None and state["days_left"] is None


async def test_silence_is_reported_once_per_phase(db):
    """An hourly watchdog must not say the same thing every hour."""
    user = await make_user(db, "waechter")
    row = await _falling(db, user, key="akku.stumm")
    row.last_at = dt.datetime.now(tz=dt.timezone.utc) - dt.timedelta(hours=30)
    await db.commit()
    inst = await _instance(db, user)

    first = await run_action(db, inst, _node({"series": "akku.stumm", "silence_hours": 24}))
    second = await run_action(db, inst, _node({"series": "akku.stumm", "silence_hours": 24}))

    assert first["silent"] is True and first["report_silence"] is True
    assert second["silent"] is True and second["report_silence"] is False


async def test_a_fresh_series_is_not_silent(db):
    user = await make_user(db, "waechter")
    await _falling(db, user, key="akku.frisch")
    inst = await _instance(db, user)

    out = await run_action(db, inst, _node({"series": "akku.frisch", "silence_hours": 24}))

    assert out["silent"] is False and out["report_silence"] is False


async def test_a_text_series_answers_with_its_newest_entry(db):
    """A heading and a body have no direction, but "when did something last arrive" is the
    same question for both kinds."""
    user = await make_user(db, "waechter")
    row = Series(owner_user_id=user.id, key="ki-tech-news", kind="text", name="News")
    db.add(row)
    await db.flush()
    now = dt.datetime.now(tz=dt.timezone.utc)
    db.add(SeriesPoint(series_id=row.id, ts=now - dt.timedelta(days=1),
                       title="Gestern", body="alt", format="markdown"))
    db.add(SeriesPoint(series_id=row.id, ts=now, title="Heute", body="neu",
                       format="markdown"))
    row.points = 2
    row.last_at = now
    await db.commit()
    inst = await _instance(db, user)

    out = await run_action(db, inst, _node({"series": "ki-tech-news"}))

    state = inst.context["series"]
    assert out["kind"] == "text"
    assert state["title"] == "Heute" and state["body"] == "neu"
    assert state["points"] == 2 and state["age_hours"] < 1


async def test_the_key_comes_out_of_the_context(db):
    user = await make_user(db, "waechter")
    await _falling(db, user, key="akku.shelter")
    inst = await _instance(db, user)
    inst.context = {"was": {"key": "akku.shelter"}}

    await run_action(db, inst, _node({"series": "{{was.key}}"}))

    assert inst.context["series"]["value"] == 50.0


async def test_the_answer_lands_where_the_flow_asked_for_it(db):
    user = await make_user(db, "waechter")
    await _falling(db, user, key="akku.shelter")
    inst = await _instance(db, user)

    await run_action(db, inst, _node({"series": "akku.shelter", "context_key": "akku"}))

    assert inst.context["akku"]["value"] == 50.0
    assert (await db.execute(select(Series))).scalars().first() is not None


async def test_it_warns_once_before_the_series_runs_out(db):
    """The battery pre-warning: the flow branches on it, so it must fire once and not every
    hour, and it must come back after a refill."""
    user = await make_user(db, "waechter")
    row = await _falling(db, user, key="akku.warnt")
    inst = await _instance(db, user)

    first = await run_action(db, inst, _node({
        "series": "akku.warnt", "target": 0, "forewarn_days": 14}))
    second = await run_action(db, inst, _node({
        "series": "akku.warnt", "target": 0, "forewarn_days": 14}))

    assert first["warn"] is True
    assert second["warn"] is False
    assert row.warned_at is not None and row.warned_value == 50.0


async def test_far_from_the_target_nothing_is_warned(db):
    user = await make_user(db, "waechter")
    await _falling(db, user, key="akku.ruhig")
    inst = await _instance(db, user)

    out = await run_action(db, inst, _node({
        "series": "akku.ruhig", "target": 0, "forewarn_days": 2}))

    assert out["warn"] is False


async def test_a_refill_lets_it_warn_again(db):
    """A new battery is a new phase: the mark expires when the value clearly rises."""
    from app.services import series as service
    user = await make_user(db, "waechter")
    row = await _falling(db, user, key="akku.neu")
    inst = await _instance(db, user)
    await run_action(db, inst, _node({
        "series": "akku.neu", "target": 0, "forewarn_days": 14}))
    assert row.warned_at is not None

    await service.ingest(db, row, [{"ts": dt.datetime.now(tz=dt.timezone.utc), "value": 100.0}])
    await db.commit()

    assert row.warned_at is None and row.warned_value is None


async def test_a_created_series_keeps_unit_and_limits(db):
    """A flow that creates a series per device would otherwise leave every one of them
    without an axis label and without a floor."""
    user = await make_user(db, "waechter")
    inst = await _instance(db, user)

    await run_action(db, inst, {"id": "w", "type": "auto_action", "data": {"config": {
        "action": {"action": "series_record", "params": {
            "series": "akku.neuling", "value": "42", "name": "Akku Neuling",
            "unit": "%", "min": 1, "max": 100}}}}})
    await db.commit()

    row = (await db.execute(select(Series).where(Series.key == "akku.neuling"))).scalar_one()
    assert row.settings == {"unit": "%", "min": 1.0, "max": 100.0}
    assert row.name == "Akku Neuling"


async def test_an_existing_series_keeps_what_a_person_set(db):
    """Only at creation: whoever adjusts the limits by hand keeps them."""
    user = await make_user(db, "waechter")
    row = Series(owner_user_id=user.id, key="akku.alt", kind="number", name="alt",
                 settings={"unit": "%", "min": 5, "max": 95})
    db.add(row)
    await db.commit()
    inst = await _instance(db, user)

    await run_action(db, inst, {"id": "w", "type": "auto_action", "data": {"config": {
        "action": {"action": "series_record", "params": {
            "series": "akku.alt", "value": "42", "unit": "V", "min": 0, "max": 1000}}}}})
    await db.commit()

    assert row.settings == {"unit": "%", "min": 5, "max": 95}
