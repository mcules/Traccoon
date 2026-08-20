"""Appending to a note out of a flow.

The vault was reachable from a flow before, over the raw `tool_call`. Only: the obsidian
server addresses a note as a `oneOf` (`{"type": "path", "path": …}`), and whoever writes
that out by hand in the arguments of every flow gets it wrong once and then wonders why the
note stays empty. What is checked here is exactly that shape, plus that a flow with nothing
to say does not fail because of it.
"""
import pytest
from app.models.enums import WorkflowSubjectKind, WorkflowVersionStatus
from app.models.workflow import WorkflowDefinition, WorkflowInstance, WorkflowVersion
from app.services import workflow_tools
from app.services.workflow_actions import run_action

from conftest import make_user

pytestmark = pytest.mark.asyncio


@pytest.fixture
def mcp_stub(monkeypatch):
    """Replace the MCP call by a transcript."""
    aufrufe = []

    async def fake(db, owner_id, name, argumente):
        aufrufe.append((name, argumente))
        return {"ok": True, "text": "angehängt", "json": None}

    monkeypatch.setattr(workflow_tools, "aufrufen", fake)
    return aufrufe


async def _instanz(db, anna, context: dict) -> WorkflowInstance:
    d = WorkflowDefinition(project_id=None, key="notiz", name="Notiz", created_by=anna.id,
                           subject_kind=WorkflowSubjectKind.standalone)
    db.add(d)
    await db.flush()
    v = WorkflowVersion(definition_id=d.id, version=1, graph={"nodes": [], "edges": []},
                        status=WorkflowVersionStatus.published)
    db.add(v)
    await db.flush()
    inst = WorkflowInstance(definition_id=d.id, version_id=v.id,
                            subject_kind=WorkflowSubjectKind.standalone,
                            context=context, started_by=anna.id)
    db.add(inst)
    await db.flush()
    return inst


def _knoten(**params) -> dict:
    return {"id": "n", "type": "auto_action", "data": {"config": {"action": {
        "action": "note_append", "params": params}}}}


async def test_pfad_und_text_kommen_aus_dem_kontext(db, mcp_stub):
    """The point of the whole thing: the kind decides the note, so a new kind writes its own
    without anybody touching the flow."""
    anna = await make_user(db, "anna")
    inst = await _instanz(db, anna, {"spam": {"art": "phishing", "befunde_text": "gibt sich als Bank aus"}})

    r = await run_action(db, inst, _knoten(
        pfad="04 Wissen/Erkennung/{{ spam.art }}.md",
        text="- {{ spam.befunde_text }}"))

    assert r["ok"] is True
    name, argumente = mcp_stub[0]
    # Mit Server-Präfix: ohne es findet die Sitzung kein Werkzeug und antwortet mit einem
    # HINWEIS ALS TEXT, den `aufrufen` als Erfolg zurückgibt. Die Notiz bliebe leer.
    assert name == "obsidian__obsidian_append_to_note"
    assert argumente["target"] == {"type": "path", "path": "04 Wissen/Erkennung/phishing.md"}
    assert argumente["content"] == "- gibt sich als Bank aus"
    assert "section" not in argumente, "ohne Abschnitt wird auch keiner mitgeschickt"
    assert inst.context["note"]["ok"] is True


async def test_werkzeug_ist_uebersteuerbar(db, mcp_stub):
    """Ein Vault an einem anderen Server soll erreichbar bleiben, ohne neue Aktion."""
    anna = await make_user(db, "anna")
    inst = await _instanz(db, anna, {})
    await run_action(db, inst, _knoten(pfad="a.md", text="x", werkzeug="zweitvault__append"))
    assert mcp_stub[0][0] == "zweitvault__append"


async def test_abschnitt_wird_durchgereicht(db, mcp_stub):
    anna = await make_user(db, "anna")
    inst = await _instanz(db, anna, {})
    await run_action(db, inst, _knoten(pfad="a.md", text="x", ueberschrift="Fälle"))
    argumente = mcp_stub[0][1]
    assert argumente["section"] == {"type": "heading", "target": "Fälle"}
    # Ohne das schlägt der Aufruf an einer Notiz fehl, die den Abschnitt noch nicht hat.
    assert argumente["createTargetIfMissing"] is True


async def test_ohne_text_wird_nichts_geschrieben(db, mcp_stub):
    """A flow that has nothing to write must not fail because of it: the note is an aside,
    not the purpose of the run."""
    anna = await make_user(db, "anna")
    inst = await _instanz(db, anna, {"spam": {}})

    r = await run_action(db, inst, _knoten(pfad="a.md", text="{{ spam.befunde_text }}"))

    assert r["ok"] is False and mcp_stub == []
    assert inst.context["note"]["ok"] is False


# ── Was passiert, wenn es das Werkzeug gar nicht gibt ────────────────────────

async def test_fehlendes_werkzeug_ist_ein_fehler_kein_text():
    """Der Fall vom 19.08.2026: Ohne Server-Präfix fand die Sitzung nichts, antwortete mit
    einem Hinweis ALS TEXT, und der Aufrufer verbuchte das als Erfolg. Die Notiz blieb leer,
    der Ablauf meldete grün."""
    from app.worker.mcp_client import McpNichtVerfuegbar, MultiMcpSession

    sitzung = MultiMcpSession()          # kein Gateway, keine Server
    with pytest.raises(McpNichtVerfuegbar):
        await sitzung.call("obsidian_append_to_note", {"content": "x"})


async def test_aufrufen_meldet_das_als_nicht_ok(db):
    """`workflow_tools.aufrufen` fängt jede Ausnahme ab; entscheidend ist, dass daraus ein
    `ok: False` wird und nicht ein Erfolg mit Prosa im Text."""
    from app.services.workflow_tools import aufrufen

    anna = await make_user(db, "anna")
    r = await aufrufen(db, anna.id, "obsidian_append_to_note", {"content": "x"})
    assert r["ok"] is False and r["error"]
