"""Tools in a flow: connecting foreign systems without programming.

The tools come from the MCP registry, the same list the agent uses. What is checked is that
a flow reaches only the tools of ITS owner, that a result lands in the context and that a
failure arrives as a failure instead of as text that looks like success.
"""
import pytest
from app.models.plugins import McpServer
from app.models.workflow import WorkflowInstance
from app.services import workflow_tools
from app.services.workflow_actions import run_action

from conftest import make_user

pytestmark = pytest.mark.asyncio


class _Sitzung:
    """MCP session as a dummy: what the real server would answer."""

    def __init__(self, antwort: str, tools: list | None = None):
        self.antwort, self.tools, self.aufrufe = antwort, tools or [], []

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False

    async def list_tools(self):
        return self.tools

    async def call(self, name, arguments):
        self.aufrufe.append((name, arguments))
        return self.antwort


class _Werkzeug:
    def __init__(self, name, beschreibung="", schema=None):
        self.name, self.description, self.schema = name, beschreibung, schema or {}


@pytest.fixture
async def anna(db):
    user = await make_user(db, "anna")
    db.add(McpServer(name="obsidian", transport="http", url="http://obsidian:3010/mcp",
                     enabled=True, user_id=user.id))
    db.add(McpServer(name="fremd", transport="http", url="http://fremd:3010/mcp",
                     enabled=True, user_id=user.id + 999))
    await db.commit()
    return user


async def test_nur_die_eigenen_server_stehen_zur_wahl(db, anna, monkeypatch):
    """A flow gets nowhere its owner is not allowed to go."""
    server = await workflow_tools._server_des_besitzers(db, anna.id)
    assert [s["name"] for s in server] == ["obsidian"]


async def test_werkzeugliste_nennt_pflichtfelder(db, anna, monkeypatch):
    sitzung = _Sitzung("", [_Werkzeug("obsidian__obsidian_append_to_note", "Anhängen\nmehr Text",
                                      {"properties": {"path": {}, "content": {}},
                                       "required": ["path"]})])
    monkeypatch.setattr(workflow_tools, "_sitzung",
                        lambda db_, owner: _fertig(sitzung))
    liste = await workflow_tools.werkzeuge(db, anna.id)
    assert liste[0]["name"] == "obsidian__obsidian_append_to_note"
    assert liste[0]["pflicht"] == ["path"]
    assert liste[0]["felder"] == ["path", "content"]
    # Only the first line of the description: the rest blows up every selection list.
    assert liste[0]["beschreibung"] == "Anhängen"


async def _fertig(wert):
    return wert


async def test_aufruf_landet_im_kontext(db, anna, monkeypatch):
    sitzung = _Sitzung('{"ok": true, "path": "Notiz.md"}')
    monkeypatch.setattr(workflow_tools, "_sitzung", lambda db_, owner: _fertig(sitzung))

    inst = WorkflowInstance(definition_id=1, version_id=1, context={"mail": {"subject": "Rechnung"}},
                            started_by=anna.id)
    node = {"id": "n1", "type": "auto_action", "data": {"config": {"action": {
        "action": "tool_call",
        "params": {"tool": "obsidian__obsidian_append_to_note",
                   "arguments": {"path": "{{mail.subject}}.md", "content": "Test"}}}}}}
    ergebnis = await run_action(db, inst, node)

    assert ergebnis["ok"] is True
    # Templates in the arguments are filled; otherwise {{mail.subject}} would stand there literally.
    assert sitzung.aufrufe == [("obsidian__obsidian_append_to_note",
                                {"path": "Rechnung.md", "content": "Test"})]
    assert inst.context["tool"]["ok"] is True
    assert inst.context["tool"]["json"] == {"ok": True, "path": "Notiz.md"}


async def test_unbekannter_server_ist_ein_fehler_kein_text(db, anna):
    """The MCP session answers an unknown server with a hint TEXT. If that passed as success,
    the flow would run on as if everything were fine."""
    r = await workflow_tools.aufrufen(db, anna.id, "gibtsnicht__tool", {})
    assert r["ok"] is False and "unknown MCP server" in r["error"]


async def test_fehler_kann_den_schritt_abbrechen(db, anna, monkeypatch):
    monkeypatch.setattr(workflow_tools, "_sitzung",
                        lambda db_, owner: _fertig(_Sitzung('{"error": "kaputt"}')))
    inst = WorkflowInstance(definition_id=1, version_id=1, context={}, started_by=anna.id)

    node = {"id": "n1", "type": "auto_action", "data": {"config": {"action": {
        "action": "tool_call", "params": {"tool": "obsidian__x", "fail_on_error": True}}}}}
    with pytest.raises(ValueError, match="kaputt"):
        await run_action(db, inst, node)

    # Without the switch the flow decides itself, over tool.ok at a branch.
    node["data"]["config"]["action"]["params"]["fail_on_error"] = False
    ergebnis = await run_action(db, inst, node)
    assert ergebnis["ok"] is False and inst.context["tool"]["ok"] is False
