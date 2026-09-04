"""Every tool the worker calls by name has to exist.

The memory wrote into the vault through a foreign MCP server. When that server
was switched off, six calls in `tools_memory`, two in the curator and two in the
runtime went on naming its tools — and every write failed with "no such server".
Nothing caught it: the tests faked a server that answered to whatever name it
was given, so they stayed green while the memory was gone. From the outside a
failed remember is not an error, it is a rule that quietly does not come back.

So the names are checked against the server that actually answers them.
"""
from __future__ import annotations

import ast
import pathlib
import re

from app.services import notes_mcp

WORKER = pathlib.Path(__file__).resolve().parents[1] / "app" / "worker"
SERVICES = pathlib.Path(__file__).resolve().parents[1] / "app" / "services"

# `mcp.call("<name>", …)` and the constants that hold such a name.
CALLED = re.compile(r'["\'](vault__[a-z_]+)["\']')

# Servers that are not the house's own are not checked here: they are somebody
# else's software and their tool list is not in this repository.
OWN_PREFIX = "vault__"


def _sources() -> list[pathlib.Path]:
    return sorted([*WORKER.rglob("*.py"), *SERVICES.rglob("*.py")])


def test_every_named_note_tool_exists() -> None:
    known = {f"{OWN_PREFIX}{t['name']}" for t in notes_mcp.toollist()}
    assert known, "the note server offers no tools at all"
    missing: list[tuple[str, str]] = []
    for path in _sources():
        for name in CALLED.findall(path.read_text(encoding="utf-8")):
            if name not in known:
                missing.append((path.name, name))
    assert not missing, f"named but not offered: {missing}"


def test_no_call_names_the_server_that_was_switched_off() -> None:
    """It was replaced on 2026-09-03. A name that comes back is a call into
    nothing, and it fails where nobody is watching."""
    gone: list[tuple[str, str]] = []
    for path in _sources():
        for hit in re.findall(r'["\']([a-z_]*obsidian[a-z_]*__[a-z_]+)["\']',
                              path.read_text(encoding="utf-8")):
            gone.append((path.name, hit))
    assert not gone, f"calls a server that is not there: {gone}"


def test_the_memory_addresses_notes_by_a_plain_path() -> None:
    """The tools of the house take `path`; the server before them took an object
    with a discriminator. A leftover of that shape reaches the server as an
    argument it does not know, which is an error per write and not per run."""
    text = (WORKER / "tools_memory.py").read_text(encoding="utf-8")
    tree = ast.parse(text)
    for node in ast.walk(tree):
        if not (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)):
            continue
        if node.func.attr not in ("call", "call_ex") or len(node.args) < 2:
            continue
        args = node.args[1]
        if not isinstance(args, ast.Dict):
            continue
        keys = {k.value for k in args.keys if isinstance(k, ast.Constant)}
        assert "target" not in keys, "a note is addressed by `path`, not by `target`"
