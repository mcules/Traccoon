"""Ein Job ist Zeitplan plus Ablauf — nicht fünf Ausführungen derselben Sache.

`kind` verzweigte über prompt, script, http, workflow und film. Vier davon taten dasselbe in
vier Ausführungen, jede mit eigener Fehlerbehandlung, eigener Benachrichtigung und der
Beschränkung, genau eins tun zu können: „erst fragen, dann prüfen, dann melden“ ging in
keiner. Geprüft wird, dass die Umstellung nichts davon verliert — und dass das Ergebnis des
Ablaufs in der Job-Historie ankommt, wo vorher nur „gestartet“ stand.
"""
import pytest
from app.models.notification import Notification
from app.models.ops import Job, JobRun
from app.models.workflow import WorkflowDefinition, WorkflowVersion
from app.services import workflow_engine
from app.services.job_modes import umstellen
from app.services.scheduler import _start_workflow_job, run_job_kind
from sqlalchemy import select

from conftest import make_user

pytestmark = pytest.mark.asyncio


async def _job(db, anna, **felder) -> Job:
    grund = {"name": "Prüfer", "type": "cron", "schedule": "0 8 * * *", "kind": "prompt",
             "user_id": anna.id, "notify_mode": "always"}
    job = Job(**{**grund, **felder})
    db.add(job)
    await db.commit()
    await db.refresh(job)
    return job


def _knoten(graph: dict) -> dict:
    return {n["id"]: n["data"]["config"] for n in graph["nodes"]}


async def _graph_von(db, job: Job) -> dict:
    d = await db.get(WorkflowDefinition, job.workflow_definition_id)
    v = await db.get(WorkflowVersion, d.current_version_id)
    return v.graph


async def test_prompt_job_wird_ein_ablauf(db):
    anna = await make_user(db, "anna")
    job = await _job(db, anna, agent="news", prompt="Fasse die Woche zusammen.")

    assert await umstellen(db) == 1
    await db.refresh(job)
    assert job.kind == "workflow" and job.workflow_definition_id

    knoten = _knoten(await _graph_von(db, job))
    arbeit = knoten["arbeit"]["action"]
    assert arbeit["action"] == "agent_run"
    assert arbeit["params"]["agent"] == "news"
    assert arbeit["params"]["task"] == "Fasse die Woche zusammen."
    # Das Ergebnis des Laufs ist die Antwort des Ablaufs — daran hängt die Job-Historie.
    assert knoten["answer"]["action"]["params"]["text"] == "{{ result.output }}"


async def test_skript_job_wird_ein_ablauf(db):
    anna = await make_user(db, "anna")
    job = await _job(db, anna, kind="script", command="pruefe.sh", args=["-x", "42"],
                     notify_mode="never")
    await umstellen(db)
    knoten = _knoten(await _graph_von(db, job))
    assert knoten["arbeit"]["action"]["params"] == {
        "command": "pruefe.sh", "args": ["-x", "42"], "timeout_sec": 600,
        "context_key": "result"}
    # `never` heißt: kein Melde-Knoten, nicht etwa eine Weiche, die nie greift.
    assert "melden" not in knoten


async def test_meldemodus_wird_zur_weiche(db):
    anna = await make_user(db, "anna")
    job = await _job(db, anna, prompt="Sieh nach.", notify_mode="on_error")
    await umstellen(db)
    knoten = _knoten(await _graph_von(db, job))
    weiche = knoten["melden_wenn"]["branches"][0]
    assert weiche["guard"] == {"==": [{"var": "result.status"}, "failed"]}
    assert knoten["melden"]["action"]["params"]["title"] == "Job: Prüfer"


async def test_langer_text_kommt_in_eine_ablage(db):
    """`result_html` verwies auf `/digest/<Lauf>` — eine Seite, die es nie gab, und der Text
    lag abgeschnitten im Ausgabefeld eines Laufs. Jetzt wird er hingelegt wie ein Messwert."""
    anna = await make_user(db, "anna")
    job = await _job(db, anna, prompt="Digest", result_html=True)
    await umstellen(db)
    graph = await _graph_von(db, job)
    knoten = _knoten(graph)
    ablegen = knoten["ablegen"]["action"]
    assert ablegen["action"] == "document"
    assert ablegen["params"]["storage"] == "pruefer" and ablegen["params"]["name"] == "Prüfer"
    # Gemeldet wird der Verweis, nicht der Text.
    assert knoten["melden"]["action"]["params"]["text"] == "{{ document.title }}\n{{ document.url }}"
    # Und das Ablegen steht VOR der Melde-Frage: auch ein stiller Job behält seinen Text.
    kanten = {(e["source"], e["target"]) for e in graph["edges"]}
    assert ("arbeit", "ablegen") in kanten and ("ablegen", "answer") in kanten


async def test_umstellung_fasst_umgestellte_nicht_wieder_an(db):
    anna = await make_user(db, "anna")
    job = await _job(db, anna, prompt="Einmal")
    assert await umstellen(db) == 1
    erste = job.workflow_definition_id
    assert await umstellen(db) == 0
    await db.refresh(job)
    assert job.workflow_definition_id == erste


async def test_film_bleibt_eine_eigene_art(db):
    """Er tut nichts weiter als sich selbst — ein Ablauf drumherum brächte nichts."""
    anna = await make_user(db, "anna")
    job = await _job(db, anna, kind="film")
    assert await umstellen(db) == 0
    await db.refresh(job)
    assert job.kind == "film"


async def test_ergebnis_landet_in_der_job_historie(db, redis_stub):
    """Vorher stand dort „Instanz #N gestartet“ — das Ergebnis musste man sich suchen."""
    anna = await make_user(db, "anna")
    job = await _job(db, anna, prompt="Sag Hallo", notify_mode="never")
    await umstellen(db)
    # Statt eines echten Agentenlaufs: der Ablauf hält gleich eine Antwort fest.
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


async def test_freier_agentenlauf_wartet_auf_sein_ergebnis(db, redis_stub, monkeypatch):
    """Der freie Lauf steckte in der Job-Art fest; als Knoten kann ihn jeder Ablauf haben."""
    from app.models.enums import WorkflowSubjectKind, WorkflowVersionStatus
    from app.services.workflow_actions import run_action

    auftraege: list[dict] = []

    async def enqueue_task(payload):
        auftraege.append(payload)

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

    ergebnis = await run_action(db, inst, {"id": "frage", "data": {"config": {"action": {
        "action": "agent_run",
        "params": {"agent": "news", "task": "Berichte über {{ was }}"}}}}})
    assert ergebnis["started"] is True and ergebnis["agent"] == "news"
    # Ohne das Warten liefe der Ablauf weiter, bevor die Antwort da ist.
    assert ergebnis["_wait"]["context_key"] == "run"
    (auftrag,) = [t for t in auftraege if t.get("kind") == "agent_frei"]
    assert auftrag["prompt"] == "Berichte über die Lage"
    assert auftrag["owner_id"] == anna.id


# ── Ablagen: wohin ein Ablauf seinen Text legt ───────────────────────────────

async def test_der_text_landet_in_der_ablage(db, redis_stub):
    """Vorher lag er im Ausgabefeld eines Laufs und die Meldung verwies ins Leere."""
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

    ergebnis = await run_action(db, inst, {"id": "ablegen", "data": {"config": {"action": {
        "action": "document",
        "params": {"storage": "rueckblick", "name": "Rückblick",
                   "text": "{{ result.output }}"}}}}})
    await db.commit()
    assert ergebnis["stored"] is True

    eintrag = (await db.execute(select(DocEntry))).scalars().one()
    assert eintrag.body.startswith("# Montag")
    # Die Überschrift kommt aus dem Text, wenn keine genannt wurde.
    assert eintrag.title == "Montag"
    ablage = (await db.execute(select(DocSeries))).scalars().one()
    assert ablage.key == "rueckblick" and ablage.last_title == "Montag"
    # Der Verweis gehört in die Meldung — er ist der Grund, warum überhaupt abgelegt wird.
    assert inst.context["document"]["url"].endswith("/documents/rueckblick")


async def test_leerer_text_legt_nichts_ab(db, redis_stub):
    """Eine leere Fassung verdrängte im Verlauf eine echte und stünde als „Stand von heute“ da."""
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

    ergebnis = await run_action(db, inst, {"id": "ablegen", "data": {"config": {"action": {
        "action": "document", "params": {"storage": "leer", "text": "{{ fehlt }}"}}}}})
    assert ergebnis["stored"] is False
    assert (await db.execute(select(DocEntry))).scalars().all() == []


async def test_alte_fassungen_werden_vergessen(db):
    """Ein täglicher Rückblick wäre nach einem Jahr 365 Fassungen."""
    from app.models.documents import DocEntry
    from app.services import documents

    anna = await make_user(db, "anna")
    for n in range(5):
        await documents.hinlegen(db, anna.id, "kurz", titel=f"Nr {n}", text=f"Text {n}",
                                 behalten=3)
    await db.commit()
    uebrig = (await db.execute(select(DocEntry.title))).scalars().all()
    assert sorted(uebrig) == ["Nr 2", "Nr 3", "Nr 4"]
