"""A job is schedule plus flow — not five executions of the same matter.

`kind` branched over prompt, script, http, workflow and film. Four of them did the same thing
in four executions, each with its own error handling, its own notification and the limit of
being able to do exactly one thing: "first ask, then check, then report" worked in none of
them. What is checked is that the conversion loses none of it — and that the result of the
flow arrives in the job history, where only "started" used to stand.
"""
import pytest
from app.models.notification import Notification
from app.models.ops import Job, JobRun
from app.models.workflow import WorkflowDefinition, WorkflowVersion
from app.services import workflow_engine
from app.services.job_modes import convert
from app.services.scheduler import _start_workflow_job, run_job_kind
from sqlalchemy import select

from conftest import make_user

pytestmark = pytest.mark.asyncio


async def _job(db, anna, **fields) -> Job:
    reason = {"name": "Prüfer", "type": "cron", "schedule": "0 8 * * *", "kind": "prompt",
             "user_id": anna.id, "notify_mode": "always"}
    job = Job(**{**reason, **fields})
    db.add(job)
    await db.commit()
    await db.refresh(job)
    return job


def _node(graph: dict) -> dict:
    return {n["id"]: n["data"]["config"] for n in graph["nodes"]}


async def _graph_from(db, job: Job) -> dict:
    d = await db.get(WorkflowDefinition, job.workflow_definition_id)
    v = await db.get(WorkflowVersion, d.current_version_id)
    return v.graph


async def test_a_prompt_job_becomes_a_flow(db):
    anna = await make_user(db, "anna")
    job = await _job(db, anna, agent="news", prompt="Fasse die Woche zusammen.")

    assert await convert(db) == 1
    await db.refresh(job)
    assert job.kind == "workflow" and job.workflow_definition_id

    node = _node(await _graph_from(db, job))
    work = node["arbeit"]["action"]
    assert work["action"] == "agent_run"
    assert work["params"]["agent"] == "news"
    assert work["params"]["task"] == "Fasse die Woche zusammen."
    # The result of the run is the answer of the flow — the job history hangs on it.
    assert node["answer"]["action"]["params"]["text"] == "{{ result.output }}"


async def test_a_script_job_becomes_a_flow(db):
    anna = await make_user(db, "anna")
    job = await _job(db, anna, kind="script", command="pruefe.sh", args=["-x", "42"],
                     notify_mode="never")
    await convert(db)
    node = _node(await _graph_from(db, job))
    assert node["arbeit"]["action"]["params"] == {
        "command": "pruefe.sh", "args": ["-x", "42"], "timeout_sec": 600,
        "context_key": "result"}
    # `never` means: no report node, not a decision that never fires.
    assert "melden" not in node


async def test_the_notify_mode_becomes_a_decision(db):
    anna = await make_user(db, "anna")
    job = await _job(db, anna, prompt="Sieh nach.", notify_mode="on_error")
    await convert(db)
    node = _node(await _graph_from(db, job))
    decision = node["melden_wenn"]["branches"][0]
    assert decision["guard"] == {"==": [{"var": "result.status"}, "failed"]}
    assert node["melden"]["action"]["params"]["title"] == "Job: Prüfer"


async def test_long_text_goes_into_a_storage(db):
    """`result_html` pointed at `/digest/<run>` — a page that never existed, and the text lay
    truncated in the output field of a run. Now it is put down like a measurement."""
    anna = await make_user(db, "anna")
    job = await _job(db, anna, prompt="Digest", result_html=True)
    await convert(db)
    graph = await _graph_from(db, job)
    node = _node(graph)
    store = node["ablegen"]["action"]
    assert store["action"] == "document"
    assert store["params"]["storage"] == "pruefer" and store["params"]["name"] == "Prüfer"
    # What is reported is the reference, not the text.
    assert node["melden"]["action"]["params"]["text"] == "{{ document.title }}\n{{ document.url }}"
    # And the storing stands BEFORE the reporting question: a silent job keeps its text too.
    edges = {(e["source"], e["target"]) for e in graph["edges"]}
    assert ("arbeit", "ablegen") in edges and ("ablegen", "answer") in edges


async def test_the_conversion_does_not_touch_the_converted_again(db):
    anna = await make_user(db, "anna")
    job = await _job(db, anna, prompt="Einmal")
    assert await convert(db) == 1
    first = job.workflow_definition_id
    assert await convert(db) == 0
    await db.refresh(job)
    assert job.workflow_definition_id == first


async def test_the_film_stays_its_own_kind(db):
    """It does nothing but itself — a flow around it would bring nothing."""
    anna = await make_user(db, "anna")
    job = await _job(db, anna, kind="film")
    assert await convert(db) == 0
    await db.refresh(job)
    assert job.kind == "film"


async def test_the_result_lands_in_the_job_history(db, redis_stub):
    """Before, "instance #N started" stood there — the result one had to go looking for."""
    anna = await make_user(db, "anna")
    job = await _job(db, anna, prompt="Sag Hallo", notify_mode="never")
    await convert(db)
    # Instead of a real agent run: the flow records an answer right away.
    d = await db.get(WorkflowDefinition, job.workflow_definition_id)
    v = await db.get(WorkflowVersion, d.current_version_id)
    v.graph = {"nodes": [
        {"id": "start", "type": "start", "position": {"x": 0, "y": 0}, "data": {"config": {}}},
        {"id": "answer", "type": "auto_action", "position": {"x": 0, "y": 1},
         "data": {"config": {"action": {"action": "answer", "params": {"text": "Hallo {{ job.name }}"}}}}},
        {"id": "ende", "type": "end", "position": {"x": 0, "y": 2},
         "data": {"config": {"outcome": "completed"}}}],
        "edges": [{"id": "a", "source": "start", "target": "answer"},
                  {"id": "b", "source": "answer", "target": "ende"}]}
    await db.commit()

    jr = JobRun(job_id=job.id, status="running")
    db.add(jr)
    await db.flush()
    await run_job_kind(db, job, jr)
    await db.commit()
    await workflow_engine.drain()
    await db.refresh(jr)
    assert jr.output == "Hallo Prüfer"
    assert jr.workflow_instance_id is not None


async def test_a_free_agent_run_waits_for_its_result(db, redis_stub, monkeypatch):
    """The free run was stuck in the job kind; as a node every flow can have it."""
    from app.models.enums import WorkflowSubjectKind, WorkflowVersionStatus
    from app.services.workflow_actions import run_action

    tasks: list[dict] = []

    async def enqueue_task(payload):
        tasks.append(payload)

    import app.services.workflow_actions as wa
    monkeypatch.setattr("app.core.redis.enqueue_task", enqueue_task)
    monkeypatch.setattr(wa, "enqueue_task", enqueue_task, raising=False)

    anna = await make_user(db, "anna")
    d = WorkflowDefinition(project_id=None, key="frag", name="Fragen", created_by=anna.id,
                           subject_kind=WorkflowSubjectKind.standalone)
    db.add(d)
    await db.flush()
    v = WorkflowVersion(definition_id=d.id, version=1, status=WorkflowVersionStatus.published,
                        graph={"nodes": [
                            {"id": "start", "type": "start", "position": {"x": 0, "y": 0},
                             "data": {"config": {}}},
                            {"id": "ende", "type": "end", "position": {"x": 0, "y": 1},
                             "data": {"config": {"outcome": "completed"}}}],
                            "edges": [{"id": "k", "source": "start", "target": "ende"}]})
    db.add(v)
    await db.flush()
    d.current_version_id = v.id
    from app.services.workflow_engine import start_workflow
    inst = await start_workflow(db, d, subject_kind=WorkflowSubjectKind.standalone,
                                context={"was": "die Lage"}, actor_id=anna.id)
    await db.commit()

    result = await run_action(db, inst, {"id": "frage", "data": {"config": {"action": {
        "action": "agent_run",
        "params": {"agent": "news", "task": "Berichte über {{ was }}"}}}}})
    assert result["started"] is True and result["agent"] == "news"
    # Without the waiting the flow would carry on before the answer is there.
    assert result["_wait"]["context_key"] == "run"
    (task,) = [t for t in tasks if t.get("kind") == "agent_frei"]
    assert task["prompt"] == "Berichte über die Lage"
    assert task["owner_id"] == anna.id


# ── Ablagen: wohin ein Ablauf seinen Text legt ───────────────────────────────

async def test_the_text_lands_in_the_storage(db, redis_stub):
    """Before, it lay in the output field of a run and the report pointed into the void."""
    from app.models.documents import DocEntry, DocSeries
    from app.services.workflow_actions import run_action
    from app.services.workflow_engine import start_workflow
    from app.models.enums import WorkflowSubjectKind, WorkflowVersionStatus
    from app.models.workflow import WorkflowVersion

    anna = await make_user(db, "anna")
    d = WorkflowDefinition(project_id=None, key="rueckblick", name="Rückblick",
                           created_by=anna.id, subject_kind=WorkflowSubjectKind.standalone)
    db.add(d)
    await db.flush()
    v = WorkflowVersion(definition_id=d.id, version=1, status=WorkflowVersionStatus.published,
                        graph={"nodes": [
                            {"id": "start", "type": "start", "position": {"x": 0, "y": 0},
                             "data": {"config": {}}},
                            {"id": "ende", "type": "end", "position": {"x": 0, "y": 1},
                             "data": {"config": {"outcome": "completed"}}}],
                            "edges": [{"id": "k", "source": "start", "target": "ende"}]})
    db.add(v)
    await db.flush()
    d.current_version_id = v.id
    inst = await start_workflow(db, d, subject_kind=WorkflowSubjectKind.standalone,
                                context={"result": {"output": "# Montag\n\nAlles ruhig."}},
                                actor_id=anna.id)
    await db.commit()

    result = await run_action(db, inst, {"id": "ablegen", "data": {"config": {"action": {
        "action": "document",
        "params": {"storage": "rueckblick", "name": "Rückblick",
                   "text": "{{ result.output }}"}}}}})
    await db.commit()
    assert result["stored"] is True

    entry = (await db.execute(select(DocEntry))).scalars().one()
    assert entry.body.startswith("# Montag")
    # The heading comes out of the text when none was named.
    assert entry.title == "Montag"
    store = (await db.execute(select(DocSeries))).scalars().one()
    assert store.key == "rueckblick" and store.last_title == "Montag"
    # The reference belongs in the report — it is the reason for storing at all.
    assert inst.context["document"]["url"].endswith("/documents/rueckblick")


async def test_empty_text_files_nothing(db, redis_stub):
    """An empty version would displace a real one in the history and stand there as "today's state"."""
    from app.models.documents import DocEntry
    from app.services.workflow_actions import run_action
    from app.services.workflow_engine import start_workflow
    from app.models.enums import WorkflowSubjectKind, WorkflowVersionStatus
    from app.models.workflow import WorkflowVersion

    anna = await make_user(db, "anna")
    d = WorkflowDefinition(project_id=None, key="leer", name="Leer", created_by=anna.id,
                           subject_kind=WorkflowSubjectKind.standalone)
    db.add(d)
    await db.flush()
    v = WorkflowVersion(definition_id=d.id, version=1, status=WorkflowVersionStatus.published,
                        graph={"nodes": [
                            {"id": "start", "type": "start", "position": {"x": 0, "y": 0},
                             "data": {"config": {}}},
                            {"id": "ende", "type": "end", "position": {"x": 0, "y": 1},
                             "data": {"config": {"outcome": "completed"}}}],
                            "edges": [{"id": "k", "source": "start", "target": "ende"}]})
    db.add(v)
    await db.flush()
    d.current_version_id = v.id
    inst = await start_workflow(db, d, subject_kind=WorkflowSubjectKind.standalone,
                                context={}, actor_id=anna.id)
    await db.commit()

    result = await run_action(db, inst, {"id": "ablegen", "data": {"config": {"action": {
        "action": "document", "params": {"storage": "leer", "text": "{{ fehlt }}"}}}}})
    assert result["stored"] is False
    assert (await db.execute(select(DocEntry))).scalars().all() == []


async def test_old_versions_are_forgotten(db):
    """A daily review would be 365 versions after a year."""
    from app.models.documents import DocEntry
    from app.services import documents

    anna = await make_user(db, "anna")
    for n in range(5):
        await documents.put(db, anna.id, "kurz", title=f"Nr {n}", text=f"Text {n}",
                                 keep=3)
    await db.commit()
    left = (await db.execute(select(DocEntry.title))).scalars().all()
    assert sorted(left) == ["Nr 2", "Nr 3", "Nr 4"]
