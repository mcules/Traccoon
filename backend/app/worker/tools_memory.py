"""Memory of the agents: learned insights as notes in the Obsidian vault (TRA-30).

The filing place is the vault, because the human should be able to see and correct what has
been learned by hand; a database table would be invisible. Under
`users.vault_memory_path` lie three kinds of note:

    Mensch.md            applies to ALL runs of this human (preferences, fixed rules)
    Agent-<rolle>.md     role specific (assistent, developer, code_reviewer …)
    Projekt-<KEY>.md     project specific

The content is deliberately plain markdown, one bullet line per insight. There is no
parsing, there are no ids and no hit counters: the text is hung into the prompt as a block,
and merging duplicates is done by the agent itself over `vergiss` plus `erinnere_dich`.

WHY THESE TOOLS EXIST AT ALL: the obsidian MCP describes `target` as a `oneOf` without a
`type` field. Models like `claude-sonnet-4-5` do not serve that: they send `target` as a
JSON string instead of an object, and every call ends in `MCP error -32602`. That is why the
model never calls obsidian itself here. It gets tools with pure string parameters, and
`_note_target` below is the only place in the house that knows the `oneOf` form. That way the
memory runs on every model.
"""
from __future__ import annotations

import datetime as dt
import logging

from sqlalchemy.ext.asyncio import AsyncSession

from ..models.user import User

log = logging.getLogger(__name__)

# How much memory goes into the prompt at most: enough for a few dozen lines, little enough
# that it does not cover the assignment.
MAX_MEMORY_CHARS = 6000
# A single insight is a sentence, not an essay.
MAX_ENTRY_CHARS = 600

AREAS = ("person", "agent", "project")

# What the areas and the tools were called until the switch to English. They stand in the
# `allowed_tools` of agents and in memory notes people wrote themselves, so both names are
# accepted — a rename must not silently take a tool away from an agent.
LEGACY_AREAS = {"mensch": "person", "projekt": "project", "agent": "agent"}
LEGACY_TOOLS = {"erinnere_dich": "remember", "vergiss": "forget",
                "gedaechtnis_suchen": "memory_search"}


def _def(name: str, desc: str, props: dict, required: list[str]) -> dict:
    return {"type": "function", "function": {
        "name": name, "description": desc,
        "parameters": {"type": "object", "properties": props, "required": required}}}


_AREA_DESC = ("Where the insight belongs: 'person' = applies always and everywhere "
              "(preferences, way of working, fixed rules) · 'agent' = for your role only · "
              "'project' = for this project only.")

MEMORY_TOOLS = [
    _def("remember",
         "Remember something PERMANENTLY for future runs — a rule, a correction or a preference "
         "of your person that still applies tomorrow. Not for details of the day, ticket facts or "
         "things that already stand in the memory. One sentence per call.",
         {"area": {"type": "string", "enum": list(AREAS), "description": _AREA_DESC},
          "text": {"type": "string", "description": "The insight as one clear sentence, phrased so "
                                                    "that it stays understandable without today's "
                                                    "context."}},
         ["area", "text"]),
    _def("forget",
         "Remove a memory that is outdated or wrong. Use this when your person changes an earlier "
         "rule as well: first `forget`, then `remember` with the new one.",
         {"area": {"type": "string", "enum": list(AREAS), "description": _AREA_DESC},
          "fragment": {"type": "string", "description": "A piece of the line to delete; every "
                                                        "matching line falls away."}},
         ["area", "fragment"]),
    _def("memory_search",
         "Search your whole memory for a keyword. Needed only when you suspect something that does "
         "not stand in the memory block delivered automatically.",
         {"query": {"type": "string", "description": "A keyword or a phrase."}},
         ["query"]),
]
MEMORY_TOOL_NAMES = {t["function"]["name"] for t in MEMORY_TOOLS} | set(LEGACY_TOOLS)

NO_MEMORY = "(no memory configured — your person has set no vault folder)"


def _note_target(path: str) -> dict:
    """The `oneOf` address of the obsidian MCP. The ONLY place that knows its form.

    It has to be an object; a string here is exactly the error `MCP error -32602` older
    models fail on when they call the MCP themselves.
    """
    return {"type": "path", "path": path}


def _safe(part: str) -> str:
    """Role or project key as part of a file name: no path changes, no separators."""
    keep = [c for c in (part or "").strip() if c.isalnum() or c in "-_ \u00e4\u00f6\u00fc\u00c4\u00d6\u00dc\u00df"]
    return "".join(keep).strip() or "unbenannt"


def note_path(root: str, area: str, agent_role: str = "", project_key: str = "") -> str | None:
    """Note path for an area, or None when the area makes no sense here.

    The file names stay German: they are notes a person opens in their vault, and renaming
    them would leave the learned insights behind in a file nothing reads any more.
    """
    root = (root or "").strip().rstrip("/")
    if not root:
        return None
    area = LEGACY_AREAS.get(area, area)
    if area == "person":
        return f"{root}/Mensch.md"
    if area == "agent":
        return f"{root}/Agent-{_safe(agent_role)}.md" if agent_role else None
    if area == "project":
        return f"{root}/Projekt-{_safe(project_key)}.md" if project_key else None
    return None


async def memory_root(db: AsyncSession, owner_id: int | None) -> str:
    """The memory folder of the owner; empty means the feature is off."""
    if not owner_id:
        return ""
    user = await db.get(User, owner_id)
    return (user.vault_memory_path or "").strip() if user else ""


# MCP errors do NOT come as an exception: `mcp_client.call` discards `isError` and returns
# the error text of the server (mcp_client.py:97-103). A missing call success is therefore
# recognisable by the text here, and a "the note does not exist yet" is the normal case, not an error.
_ERROR_MARKER = ("mcp error", "kein mcp konfiguriert", "not found", "does not exist",
                  "file_exists", "error:", "\"code\":")


def _failed(text: str) -> bool:
    low = (text or "").lower()
    return not text or any(m in low for m in _ERROR_MARKER)


async def _read_note(mcp, path: str) -> str:
    """Note content or empty (a missing note is the normal case, not the error case)."""
    try:
        out = await mcp.call("obsidian__obsidian_get_note",
                             {"format": "content", "target": _note_target(path)})
    except Exception as exc:  # noqa: BLE001
        log.debug("Memory: %s not readable (%s)", path, exc)
        return ""
    return "" if _failed(out) else out


async def read_memory(mcp, root: str, agent_role: str = "", project_key: str = "") -> str:
    """The whole relevant memory as text for the prompt (truncated).

    The order goes from the general to the specific, so that the specific one stands at the
    end and weighs more in case of doubt.
    """
    if not root:
        return ""
    chunks: list[str] = []
    for area, title in (("person", "About your person"),
                        ("agent", "For your role"),
                        ("project", "For this project")):
        path = note_path(root, area, agent_role, project_key)
        if not path:
            continue
        body = (await _read_note(mcp, path)).strip()
        if body:
            chunks.append(f"## {title}\n{body}")
    return "\n\n".join(chunks)[:MAX_MEMORY_CHARS]


async def _append_line(mcp, path: str, line: str) -> str:
    """Append a line; if the note does not exist yet, create it once."""
    try:
        out = await mcp.call("obsidian__obsidian_append_to_note",
                             {"target": _note_target(path), "content": line + "\n"})
        if not _failed(out):
            return ""
    except Exception as exc:  # noqa: BLE001
        out = str(exc)
    # Second attempt: create the note. `overwrite` stays off; if it does exist after all, the
    # call had better fail than overwrite existing memory.
    header = f"# {path.rsplit('/', 1)[-1].removesuffix('.md')}\n\n"
    try:
        new = await mcp.call("obsidian__obsidian_write_note",
                             {"target": _note_target(path), "content": header + line + "\n"})
        if not _failed(new):
            return ""
        return new
    except Exception as exc:  # noqa: BLE001
        return f"{out} / {exc}"


async def call_memory_tool(db: AsyncSession, mcp, owner_id: int | None, name: str, args: dict,
                           agent_role: str = "", project_key: str = "") -> str:
    """Dispatcher for the three memory tools. The return value is terse text for the agent."""
    root = await memory_root(db, owner_id)
    if not root:
        return NO_MEMORY

    name = LEGACY_TOOLS.get(name, name)
    if name == "memory_search":
        search = (args.get("query") or args.get("suche") or "").strip()
        if not search:
            return "ERROR: `query` is missing."
        try:
            out = await mcp.call("obsidian__obsidian_search_notes",
                                 {"mode": "text", "query": search, "pathPrefix": root})
        except Exception as exc:  # noqa: BLE001
            return f"ERROR while searching: {exc}"
        return (out or "Nothing found.")[:4000]

    area = (args.get("area") or args.get("bereich") or "").strip().lower()
    area = LEGACY_AREAS.get(area, area)
    if area not in AREAS:
        return f"ERROR: `area` has to be {' | '.join(AREAS)}."
    path = note_path(root, area, agent_role, project_key)
    if not path:
        missing = "project" if area == "project" else "role"
        return (f"ERROR: the area '{area}' does not work in this run — there is no {missing}. "
                "Take 'person'.")

    if name == "remember":
        text = " ".join((args.get("text") or "").split())[:MAX_ENTRY_CHARS]
        if not text:
            return "ERROR: `text` is missing."
        today = dt.datetime.now().strftime("%Y-%m-%d")
        error = await _append_line(mcp, path, f"- [{today}] {text}")
        if error:
            return f"ERROR while remembering: {error}"
        return f"Noted in {path}."

    if name == "forget":
        ask = (args.get("fragment") or args.get("textfragment") or "").strip().lower()
        if not ask:
            return "ERROR: `fragment` is missing."
        body = await _read_note(mcp, path)
        if not body:
            return f"Nothing to forget — {path} is empty or does not exist."
        keep = [ln for ln in body.splitlines() if ask not in ln.lower()]
        removed = len(body.splitlines()) - len(keep)
        if not removed:
            return f"No line in {path} contains '{ask}' — nothing changed."
        try:
            out = await mcp.call("obsidian__obsidian_write_note",
                                 {"target": _note_target(path), "overwrite": True,
                                  "content": "\n".join(keep).rstrip() + "\n"})
        except Exception as exc:  # noqa: BLE001
            return f"ERROR while forgetting: {exc}"
        if _failed(out):
            return f"ERROR while forgetting: {out}"
        return f"{removed} line(s) removed from {path}."

    return f"ERROR: unknown memory tool '{name}'."


REFLECTION_PROMPT = (
    "A look back at this run. Was there a correction, a rule or a preference of your person "
    "that applies TO FUTURE RUNS AS WELL? Note it with `remember` in the matching area — one "
    "sentence per call, without a reference to today's ticket, so that it stays understandable "
    "on its own later.\n\n"
    "Do NOT note: details of the day, ticket facts, intermediate states, technical particulars of "
    "this assignment, and nothing that already stands in the memory block above. If a memory "
    "standing there is outdated by today, correct it (`forget`, then `remember`).\n\n"
    "If you learned nothing lasting — the normal case — call NO tool and answer with \"nothing\" "
    "only."
)
