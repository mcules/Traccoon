"""The assistant can change inbound webhooks and flow graphs itself.

The occasion: a ticket whose plan was a pure setting inside Traccoon (a webhook's event
filter, a branch in its flow) ended with the developer asking the person to do it by hand.
Handing the ticket to the assistant only helps when the assistant has the tools for it.
"""
import json

import pytest
from app.models.ops import WebhookSub
from app.models.workflow import WorkflowDefinition, WorkflowVersion
from app.worker.tools_traccoon import TRACCOON_GATED_TOOLS, call_traccoon_tool
from conftest import make_user
from sqlalchemy import select


@pytest.fixture
async def anna(db):
    return await make_user(db, "anna")


async def _tool(db, user, tool, **args) -> str:
    return await call_traccoon_tool(db, user.id, tool, args)


async def _hook(db, owner_id, route="alarm"):
    w = WebhookSub(owner_user_id=owner_id, route=route, public_id=f"pub-{route}",
                   mode="workflow", event_header="payload:event.type",
                   event_filter="alarm, ignitionOn", secret="s3cret")
    db.add(w)
    await db.commit()
    return w


async def test_a_webhook_is_listed_read_and_changed(db, anna):
    w = await _hook(db, anna.id)
    assert "alarm, ignitionOn" in await _tool(db, anna, "traccoon_list_webhooks")
    full = json.loads(await _tool(db, anna, "traccoon_get_webhook", webhook_id=w.id))
    assert full["event_header"] == "payload:event.type" and "secret" not in full

    out = await _tool(db, anna, "traccoon_update_webhook", webhook_id=w.id,
                      event_filter="alarm, deviceMoving, deviceStopped",
                      event_cooldowns={"deviceStopped": 300}, route="shelter")
    assert "changed" in out
    await db.refresh(w)
    assert w.event_filter == "alarm, deviceMoving, deviceStopped"
    assert w.event_cooldowns == {"deviceStopped": 300}
    assert w.route == "shelter"
    # The sender's side of the contract stays as it was.
    assert w.public_id == "pub-alarm" and w.secret == "s3cret"


async def test_a_foreign_webhook_is_invisible_and_a_global_one_read_only(db, anna):
    bob = await make_user(db, "bob")
    foreign = await _hook(db, bob.id, "bobs")
    shared = await _hook(db, None, "shared")
    assert "bobs" not in await _tool(db, anna, "traccoon_list_webhooks")
    assert await _tool(db, anna, "traccoon_get_webhook", webhook_id=foreign.id) == "Webhook not found."
    assert "shared" in await _tool(db, anna, "traccoon_get_webhook", webhook_id=shared.id)
    out = await _tool(db, anna, "traccoon_update_webhook", webhook_id=shared.id, route="mine")
    assert "may not change" in out
    await db.refresh(shared)
    assert shared.route == "shared"


def _graph(with_branch: bool) -> dict:
    nodes = [
        {"id": "start", "type": "start", "position": {"x": 0, "y": 0},
         "data": {"config": {"label": "in", "trigger": {"kind": "webhook", "sample": {"event": {"type": "alarm"}}}}}},
        {"id": "say", "type": "auto_action", "position": {"x": 0, "y": 100},
         "data": {"config": {"label": "say", "action": {"action": "notify", "params": {"title": "t", "text": "x"}}}}},
        {"id": "end", "type": "end", "position": {"x": 0, "y": 200}, "data": {"config": {"label": "end"}}},
    ]
    edges = [{"id": "e1", "source": "start", "target": "say"}, {"id": "e2", "source": "say", "target": "end"}]
    if with_branch:
        nodes.insert(1, {"id": "which", "type": "decision", "position": {"x": 0, "y": 50},
                         "data": {"config": {"label": "which?", "branches": [
                             {"handle": "stop", "label": "stopped",
                              "guard": {"==": [{"var": "event.type"}, "deviceStopped"]}},
                             {"handle": "rest", "label": "rest"}], "default_handle": "rest"}}})
        edges = [{"id": "e1", "source": "start", "target": "which"},
                 {"id": "e2", "source": "which", "target": "say", "sourceHandle": "stop"},
                 {"id": "e3", "source": "which", "target": "end", "sourceHandle": "rest"},
                 {"id": "e4", "source": "say", "target": "end"}]
    return {"nodes": nodes, "edges": edges}


async def _flow(db, owner_id) -> WorkflowDefinition:
    d = WorkflowDefinition(key="alarm-flow", name="Alarm", subject_kind="standalone",
                           created_by=owner_id, enabled=True)
    db.add(d)
    await db.flush()
    v = WorkflowVersion(definition_id=d.id, version=1, graph=_graph(False), status="published",
                        created_by=owner_id, notes="")
    db.add(v)
    await db.flush()
    d.current_version_id = v.id
    await db.commit()
    return d


async def test_the_graph_is_read_and_a_change_published(db, anna):
    d = await _flow(db, anna.id)
    out = await _tool(db, anna, "traccoon_get_workflow", workflow_id=d.id)
    assert "published v1" in out and '"id": "say"' in out

    out = await _tool(db, anna, "traccoon_save_workflow", workflow_id=d.id, graph=_graph(True),
                      notes="branch on the event type", publish=True)
    assert "v2" in out and "published" in out
    await db.refresh(d)
    live = await db.get(WorkflowVersion, d.current_version_id)
    assert live.version == 2 and any(n["id"] == "which" for n in live.graph["nodes"])


async def test_a_broken_graph_is_not_saved(db, anna):
    d = await _flow(db, anna.id)
    broken = _graph(True)
    broken["edges"].append({"id": "e9", "source": "which", "target": "nowhere", "sourceHandle": "stop"})
    out = await _tool(db, anna, "traccoon_save_workflow", workflow_id=d.id, graph=broken, publish=True)
    assert "Not saved" in out
    versions = (await db.execute(select(WorkflowVersion).where(
        WorkflowVersion.definition_id == d.id))).scalars().all()
    assert len(versions) == 1


def test_changing_arrangements_passes_the_gate():
    assert {"traccoon_save_workflow", "traccoon_update_webhook"} <= TRACCOON_GATED_TOOLS
