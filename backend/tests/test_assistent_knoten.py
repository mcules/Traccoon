"""Assistent als Knoten — und die Antwort eines Ablaufs an seinen Auslöser.

Bis hierher konnte nur der Mail-Eingang dem Assistenten Arbeit geben; sein Auftrag stand in
einer Mail. Geprüft wird die Mechanik des allgemeinen Weges: dass ein Knoten einen Auftrag
anlegt und einreiht, dass „warten" das Ergebnis in den Kontext holt, und dass ein Webhook die
Antwort zurückbekommt, die der Ablauf selbst geschrieben hat.
"""
import pytest
from app.models.assistant import AssistantTask
from app.models.enums import (
    WorkflowInstanceStatus, WorkflowSubjectKind, WorkflowVersionStatus,
)
from app.models.ops import WebhookSub
from app.models.workflow import WorkflowDefinition, WorkflowInstance, WorkflowVersion
from app.services.workflow_engine import start_workflow
from sqlalchemy import select

from conftest import make_user

pytestmark = pytest.mark.asyncio


def _graph(auftrag_params: dict, mit_antwort: bool = False) -> dict:
    knoten = [
        {"id": "s", "type": "start", "position": {"x": 0, "y": 0},
         "data": {"config": {"label": "Start", "trigger": {"kind": "webhook"}}}},
        {"id": "beauftragen", "type": "auto_action", "position": {"x": 0, "y": 1},
         "data": {"config": {"label": "Assistent",
                             "action": {"action": "assistent_auftrag",
                                        "params": auftrag_params}}}},
    ]
    kanten = [{"id": "e1", "source": "s", "target": "beauftragen"}]
    vorher = "beauftragen"
    if mit_antwort:
        knoten.append({"id": "antwort", "type": "auto_action", "position": {"x": 0, "y": 2},
                       "data": {"config": {"label": "Antwort",
                                           "action": {"action": "antwort", "params": {
                                               "felder": {"ergebnis": "{{ assistent.output }}",
                                                          "quelle": "{{ doc_id }}"}}}}}})
        kanten.append({"id": "e2", "source": vorher, "target": "antwort"})
        vorher = "antwort"
    knoten.append({"id": "ende", "type": "end", "position": {"x": 0, "y": 3},
                   "data": {"config": {"outcome": "completed"}}})
    kanten.append({"id": "e3", "source": vorher, "target": "ende"})
    return {"nodes": knoten, "edges": kanten}


async def _warte_bis_fertig(db, instanz_id: int, sekunden: float = 3.0):
    """Dem Wächter Zeit geben: er wartet auf den Lauf und schaltet dann selbst weiter.

    Ohne dieses Zusehen prüfte der Test den Zustand, bevor der Hintergrund-Schritt überhaupt
    an der Reihe war — und hätte „unfertig" gemeldet, wo nur „noch nicht dran" stand.
    """
    import asyncio

    for _ in range(int(sekunden / 0.02)):
        await asyncio.sleep(0.02)
        db.expire_all()
        inst = await db.get(WorkflowInstance, instanz_id)
        if inst is not None and inst.status not in (WorkflowInstanceStatus.running,
                                                    WorkflowInstanceStatus.waiting):
            return inst
    db.expire_all()
    return await db.get(WorkflowInstance, instanz_id)


async def _definition(db, name: str, graph: dict, besitzer):
    d = WorkflowDefinition(project_id=None, key=name, name=name, created_by=besitzer.id,
                           subject_kind=WorkflowSubjectKind.standalone)
    db.add(d)
    await db.flush()
    v = WorkflowVersion(definition_id=d.id, version=1, graph=graph,
                        status=WorkflowVersionStatus.published)
    db.add(v)
    await db.flush()
    d.current_version_id = v.id
    await db.commit()
    return d


async def test_knoten_legt_auftrag_an_und_reiht_ihn_ein(db):
    """Ohne Mail und ohne Ticket: der Auftrag steht im Knoten und wird gerendert."""
    user = await make_user(db, "chefin")
    d = await _definition(db, "auftrag", _graph({
        "auftrag": "Lies Dokument {{ doc_id }} und halte Wissenswertes fest.",
        "titel": "Dokument {{ doc_id }}"}), user)
    inst = await start_workflow(db, d, subject_kind=WorkflowSubjectKind.standalone,
                                context={"doc_id": "3464"}, actor_id=user.id)

    task = (await db.execute(select(AssistantTask))).scalars().one()
    assert task.kind == "auftrag" and task.status == "approved"
    assert task.owner_user_id == user.id
    assert task.title == "Dokument 3464"
    # Der Auftrag ist der Prompt — der Worker nimmt ihn aus `meta.prompt`.
    assert task.meta["prompt"] == "Lies Dokument 3464 und halte Wissenswertes fest."
    assert task.meta["agent"] == "assistent"
    # Ohne „warten" läuft der Ablauf weiter, statt am Assistenten zu hängen.
    assert inst.status == WorkflowInstanceStatus.completed


async def test_freigabe_haelt_den_auftrag_im_eingang(db):
    """Mit Freigabe wartet der Auftrag auf den Menschen, statt sofort zu laufen."""
    user = await make_user(db, "vorsichtig")
    d = await _definition(db, "freigabe", _graph({
        "auftrag": "Mach etwas", "freigabe": True}), user)
    await start_workflow(db, d, subject_kind=WorkflowSubjectKind.standalone,
                         context={}, actor_id=user.id)
    task = (await db.execute(select(AssistantTask))).scalars().one()
    assert task.status == "new"


async def test_warten_holt_das_ergebnis_in_den_kontext(db, redis_stub):
    """Mit „warten" steht die Antwort des Assistenten im Kontext — und damit im Ablauf."""
    redis_stub["*"] = {"status": "done", "output": "nichts wissenswertes",
                       "summary": "nichts wissenswertes"}
    user = await make_user(db, "geduldig")
    d = await _definition(db, "warten", _graph(
        {"auftrag": "Lies {{ doc_id }}", "warten": True}, mit_antwort=True), user)
    inst = await start_workflow(db, d, subject_kind=WorkflowSubjectKind.standalone,
                                context={"doc_id": "77"}, actor_id=user.id)

    frisch = await _warte_bis_fertig(db, inst.id)
    assert frisch.context["assistent"]["output"] == "nichts wissenswertes"
    # Die Antwort des Ablaufs ist gerendert, nicht die Vorlage.
    assert frisch.context["antwort"] == {"ergebnis": "nichts wissenswertes", "quelle": "77"}
    assert frisch.status == WorkflowInstanceStatus.completed


def _nur_antwort() -> dict:
    """Ablauf, der ohne Umweg antwortet — dafür braucht es keinen Agenten."""
    return {"nodes": [
        {"id": "s", "type": "start", "position": {"x": 0, "y": 0},
         "data": {"config": {"trigger": {"kind": "webhook"}}}},
        {"id": "antwort", "type": "auto_action", "position": {"x": 0, "y": 1},
         "data": {"config": {"action": {"action": "antwort", "params": {
             "felder": {"ergebnis": "{{ text }}", "wer": "Traccoon"}}}}}},
        {"id": "ende", "type": "end", "position": {"x": 0, "y": 2},
         "data": {"config": {"outcome": "completed"}}},
    ], "edges": [{"id": "e1", "source": "s", "target": "antwort"},
                 {"id": "e2", "source": "antwort", "target": "ende"}]}


async def test_webhook_bekommt_die_antwort_des_ablaufs(db, client):
    """Ein Webhook ist ein Auslöser — und darf eine Antwort haben."""
    user = await make_user(db, "aufrufer")
    d = await _definition(db, "rueckkanal", _nur_antwort(), user)
    hook = WebhookSub(public_id="test-hook-1", owner_user_id=user.id, route="rueckkanal",
                      mode="workflow", workflow_definition_id=d.id, response_timeout=5)
    db.add(hook)
    await db.commit()

    antwort = await client.post("/hooks/test-hook-1", json={"text": "fertig"})
    assert antwort.status_code == 200
    # Der Rumpf IST die Antwort des Ablaufs, ohne Hülle drumherum.
    assert antwort.json() == {"ergebnis": "fertig", "wer": "Traccoon"}


async def test_webhook_ohne_antwort_quittiert_und_sagt_es(db, client):
    """Läuft der Ablauf noch, kommt keine erfundene Antwort, sondern eine Auskunft."""
    user = await make_user(db, "ungeduldig")
    # Ein Ablauf, der auf den Assistenten wartet und nie ein Ergebnis bekommt
    # (redis_stub ohne Eintrag): die Zeitgrenze greift.
    d = await _definition(db, "haengt", _graph({"auftrag": "Tu was", "warten": True}), user)
    hook = WebhookSub(public_id="test-hook-2", owner_user_id=user.id, route="haengt",
                      mode="workflow", workflow_definition_id=d.id, response_timeout=1)
    db.add(hook)
    await db.commit()

    antwort = await client.post("/hooks/test-hook-2", json={})
    assert antwort.status_code == 202
    rumpf = antwort.json()
    assert rumpf["antwort"] is None and rumpf["accepted"] is True
