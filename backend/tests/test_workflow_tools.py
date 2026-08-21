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


class _Session:
    """MCP session as a dummy: what the real server would answer."""

    def __init__(self, answer: str, tools: list | None = None):
        self.answer, self.tools, self.calls = answer, tools or [], []

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False

    async def list_tools(self):
        return self.tools

    async def call(self, name, arguments):
        self.calls.append((name, arguments))
        return self.answer


class _Tool:
    def __init__(self, name, description="", schema=None):
        self.name, self.description, self.schema = name, description, schema or {}


@pytest.fixture
async def anna(db):
    user = await make_user(db, "anna")
    db.add(McpServer(name="obsidian", transport="http", url="http://obsidian:3010/mcp",
                     enabled=True, user_id=user.id))
    db.add(McpServer(name="fremd", transport="http", url="http://fremd:3010/mcp",
                     enabled=True, user_id=user.id + 999))
    await db.commit()
    return user


async def test_only_ones_own_servers_are_on_offer(db, anna, monkeypatch):
    """A flow gets nowhere its owner is not allowed to go."""
    server = await workflow_tools._server_of_owner(db, anna.id)
    assert [s["name"] for s in server] == ["obsidian"]


async def test_the_tool_list_names_the_required_fields(db, anna, monkeypatch):
    session = _Session("", [_Tool("obsidian__obsidian_append_to_note", "Anhängen\nmehr Text",
                                      {"properties": {"path": {}, "content": {}},
                                       "required": ["path"]})])
    monkeypatch.setattr(workflow_tools, "_session",
                        lambda db_, owner: _done(session))
    listing = await workflow_tools.tools(db, anna.id)
    assert listing[0]["name"] == "obsidian__obsidian_append_to_note"
    assert listing[0]["pflicht"] == ["path"]
    assert listing[0]["felder"] == ["path", "content"]
    # Only the first line of the description: the rest blows up every selection list.
    assert listing[0]["beschreibung"] == "Anhängen"


async def _done(value):
    return value


async def test_the_call_lands_in_the_context(db, anna, monkeypatch):
    session = _Session('{"ok": true, "path": "Notiz.md"}')
    monkeypatch.setattr(workflow_tools, "_session", lambda db_, owner: _done(session))

    inst = WorkflowInstance(definition_id=1, version_id=1, context={"mail": {"subject": "Rechnung"}},
                            started_by=anna.id)
    node = {"id": "n1", "type": "auto_action", "data": {"config": {"action": {
        "action": "tool_call",
        "params": {"tool": "obsidian__obsidian_append_to_note",
                   "arguments": {"path": "{{mail.subject}}.md", "content": "Test"}}}}}}
    result = await run_action(db, inst, node)

    assert result["ok"] is True
    # Templates in the arguments are filled; otherwise {{mail.subject}} would stand there literally.
    assert session.calls == [("obsidian__obsidian_append_to_note",
                                {"path": "Rechnung.md", "content": "Test"})]
    assert inst.context["tool"]["ok"] is True
    assert inst.context["tool"]["json"] == {"ok": True, "path": "Notiz.md"}


async def test_an_unknown_server_is_an_error_not_text(db, anna):
    """The MCP session answers an unknown server with a hint TEXT. If that passed as success,
    the flow would run on as if everything were fine."""
    r = await workflow_tools.call(db, anna.id, "gibtsnicht__tool", {})
    assert r["ok"] is False and "unknown MCP server" in r["error"]


async def test_an_error_can_abort_the_step(db, anna, monkeypatch):
    monkeypatch.setattr(workflow_tools, "_session",
                        lambda db_, owner: _done(_Session('{"error": "kaputt"}')))
    inst = WorkflowInstance(definition_id=1, version_id=1, context={}, started_by=anna.id)

    node = {"id": "n1", "type": "auto_action", "data": {"config": {"action": {
        "action": "tool_call", "params": {"tool": "obsidian__x", "fail_on_error": True}}}}}
    with pytest.raises(ValueError, match="kaputt"):
        await run_action(db, inst, node)

    # Without the switch the flow decides itself, over tool.ok at a branch.
    node["data"]["config"]["action"]["params"]["fail_on_error"] = False
    result = await run_action(db, inst, node)
    assert result["ok"] is False and inst.context["tool"]["ok"] is False
