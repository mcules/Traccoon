"""Tool groups that are fetched when the task turns out to need them.

Everything an agent was allowed used to stand in the prompt before it had read a word of
the task. Measured on 2026-09-09: 293 schemas and 75k tokens of context on the first call,
141 of those schemas one game's while the task was about notes, and every one of the 40
turns paid for all of it.

What is tested here is the part that decides which of them a run carries: the catalogue the
model reads instead of the schemas, and the rule that says what the catalogue may name.
"""
from __future__ import annotations

from app.worker.mcp_client import McpTool
from app.worker.runtime import AgentDef, _catalogue, _group_of


def _tool(name: str) -> McpTool:
    return McpTool(name, f"does {name}", {"type": "object", "properties": {}})


def _agent(allowed: list[str], autoload: list[str]) -> AgentDef:
    return AgentDef(
        id=1, name="assistent", role="assistent", system_prompt="",
        provider="claude_code", model="claude-opus-5", token_name="",
        fallback=None, fallback_model="", fallback_token_name="",
        temperature=0.3, max_tokens=16384, max_iterations=80,
        can_code=False, can_read_code=False, can_delegate=False, web_search=False,
        allowed_tools=allowed, allowed_skills=[], autoload_skills=[], delegate_to=[],
        autoload_tools=autoload,
    )


def test_a_tool_belongs_to_the_server_in_front_of_the_double_underscore() -> None:
    assert _group_of("uniwar__planet_get") == "uniwar"
    # The house's own tools have no server, and they must not end up in a group: they are
    # in the prompt from the first turn and there is nothing to fetch.
    assert _group_of("fs_read") == ""


def test_the_catalogue_names_the_group_its_size_and_what_it_is_about() -> None:
    deferred = {t.name: t for t in
                [_tool(f"uniwar__planet_{i}") for i in range(9)] + [_tool("vault__notes_read")]}
    text = _catalogue(deferred)
    assert "`uniwar` (9)" in text
    assert "`vault` (1)" in text
    # A sample, not a tool list — the catalogue exists to be short.
    assert text.count("planet_") <= 6
    assert "…" in text
    # It has to say that fetching is the model's own decision; the whole point is that
    # nobody has to put a keyword in the task.
    assert "load_tools" in text


def test_without_anything_deferred_there_is_no_catalogue() -> None:
    """An agent whose groups are all loaded must not be told about an empty list."""
    assert _catalogue({}) == ""


def test_what_the_allowlist_forbids_reaches_neither_the_prompt_nor_the_catalogue() -> None:
    """The catalogue must not advertise a group the agent would then be refused.

    This is the split as `run_agent` makes it. It is repeated here rather than called,
    because the original sits inside an `async with` around a live gateway session.
    """
    agent = _agent(allowed=["vault__*", "uniwar__*"], autoload=["vault"])
    tools = [_tool("vault__notes_read"), _tool("uniwar__planet_get"),
             _tool("banking__list_accounts")]

    allowed = [t for t in tools if agent.tool_allowed(t.name)]
    prompt = [t.name for t in allowed if _group_of(t.name) in set(agent.autoload_tools)]
    deferred = {t.name: t for t in allowed if _group_of(t.name) not in set(agent.autoload_tools)}

    assert prompt == ["vault__notes_read"]
    assert list(deferred) == ["uniwar__planet_get"]
    assert "banking" not in _catalogue(deferred)


def test_loading_a_tool_is_never_gated_by_the_allowlist() -> None:
    """`load_tools` is loop mechanics. An agent that cannot call it can never get a group,
    and `allowed_tools` is deny by default — so it has to stand outside that gate."""
    assert _agent(allowed=[], autoload=[]).tool_allowed("load_tools")
