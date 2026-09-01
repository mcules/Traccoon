"""`series_record` fills the extra fields of a point, and names its source from the context.

Both were gaps rather than decisions: `SeriesPoint.context` exists in the model and no flow
could reach it, and `source` was the one parameter of the action that was not templated. A
sender that carries several origins in one delivery (a phone reading from two health
sources, say) lost that distinction, and everything a device sends along besides the bare
number was dropped on the floor.
"""
from app.models.enums import WorkflowSubjectKind, WorkflowVersionStatus
from app.models.series import Series, SeriesPoint
from app.models.workflow import WorkflowDefinition, WorkflowInstance, WorkflowVersion
from app.services.workflow_actions import run_action
from conftest import make_user
from sqlalchemy import select


async def _series(db, owner, key="health.blood-pressure-systolic", kind="number") -> Series:
    row = Series(owner_user_id=owner.id, key=key, kind=kind, name=key)
    db.add(row)
    await db.commit()
    await db.refresh(row)
    return row


async def _instance(db, owner, context: dict) -> WorkflowInstance:
    definition = WorkflowDefinition(project_id=None, key="health", name="Gesundheit",
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
                            context=context, started_by=owner.id)
    db.add(inst)
    await db.flush()
    return inst


def _node(config: dict) -> dict:
    return {"id": "write", "data": {"config": config}}


async def test_a_path_hands_the_extra_fields_over_whole(db):
    """The shape a sender uses that already groups its extras, like the loop's list."""
    user = await make_user(db, "traeger")
    await _series(db, user)
    inst = await _instance(db, user, {
        "element": {"series": "health.blood-pressure-systolic", "value": 128,
                    "context": {"pulse": 69, "medication_taken": False}},
    })

    await run_action(db, inst, _node({
        "action": "series_record",
        "series": "{{element.series}}",
        "value": "{{element.value}}",
        "context": "element.context",
    }))
    await db.commit()

    point = (await db.execute(select(SeriesPoint))).scalar_one()
    assert point.value == 128
    assert point.context == {"pulse": 69, "medication_taken": False}


async def test_braces_around_the_path_are_allowed(db):
    """Whoever writes every other parameter as a template writes this one that way too."""
    user = await make_user(db, "traeger")
    await _series(db, user)
    inst = await _instance(db, user, {"element": {"value": 128, "context": {"pulse": 69}}})

    await run_action(db, inst, _node({
        "action": "series_record", "series": "health.blood-pressure-systolic",
        "value": "{{element.value}}", "context": "{{element.context}}",
    }))
    await db.commit()

    assert (await db.execute(select(SeriesPoint))).scalar_one().context == {"pulse": 69}


async def test_a_dict_is_templated_field_by_field(db):
    """For a flow that assembles the extras itself instead of passing an object through."""
    user = await make_user(db, "traeger")
    await _series(db, user, key="health.steps")
    inst = await _instance(db, user, {"element": {"value": 1200, "start": "2026-09-01T08:00:00Z"}})

    await run_action(db, inst, _node({
        "action": "series_record", "series": "health.steps",
        "value": "{{element.value}}",
        "context": {"start": "{{element.start}}", "quelle": "watch"},
    }))
    await db.commit()

    assert (await db.execute(select(SeriesPoint))).scalar_one().context == {
        "start": "2026-09-01T08:00:00Z", "quelle": "watch"}


async def test_a_text_point_carries_the_extra_fields_as_well(db):
    user = await make_user(db, "traeger")
    await _series(db, user, key="health.sleep", kind="text")
    inst = await _instance(db, user, {
        "element": {"body": '{"stages": []}', "context": {"duration_s": 17100}}})

    await run_action(db, inst, _node({
        "action": "series_record", "series": "health.sleep",
        "body": "{{element.body}}", "context": "element.context",
    }))
    await db.commit()

    point = (await db.execute(select(SeriesPoint))).scalar_one()
    assert point.body == '{"stages": []}'
    assert point.context == {"duration_s": 17100}


async def test_without_the_parameter_the_point_stays_bare(db):
    """Every flow written before this keeps behaving exactly as it did."""
    user = await make_user(db, "traeger")
    await _series(db, user, key="health.heart-rate")
    inst = await _instance(db, user, {"element": {"value": 61}})

    await run_action(db, inst, _node({
        "action": "series_record", "series": "health.heart-rate",
        "value": "{{element.value}}"}))
    await db.commit()

    assert (await db.execute(select(SeriesPoint))).scalar_one().context == {}


async def test_the_source_is_templated(db):
    """One delivery can carry points of several origins, and the point should say which."""
    user = await make_user(db, "traeger")
    await _series(db, user, key="health.heart-rate")
    inst = await _instance(db, user, {"element": {"value": 61, "source": "samsung-health"}})

    await run_action(db, inst, _node({
        "action": "series_record", "series": "health.heart-rate",
        "value": "{{element.value}}", "source": "{{element.source}}"}))
    await db.commit()

    assert (await db.execute(select(SeriesPoint))).scalar_one().source == "samsung-health"


async def test_a_fixed_source_still_works(db):
    user = await make_user(db, "traeger")
    await _series(db, user, key="health.heart-rate")
    inst = await _instance(db, user, {"element": {"value": 61}})

    await run_action(db, inst, _node({
        "action": "series_record", "series": "health.heart-rate",
        "value": "{{element.value}}", "source": "health-bridge"}))
    await db.commit()

    assert (await db.execute(select(SeriesPoint))).scalar_one().source == "health-bridge"
