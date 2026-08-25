"""Memory of the agents: learned insights as notes in the Obsidian vault.

The filing place is the vault, because the human should be able to see and correct what has
been learned by hand; a database table would be invisible. Under
`users.vault_memory_path` lie three kinds of note:

    Mensch.md                        applies to ALL runs of this human (preferences, rules)
    Agent-<rolle>.md                 role specific (assistent, developer, code_reviewer …)
    Projekt-<KEY>.md                 project specific, across every role
    Projekt-<KEY>-Agent-<rolle>.md   role specific INSIDE one project

The fourth note exists because the third one was never written: what a developer learns in
one project is wrong in the next (role note) and wrong for the reviewer of the same project
(project note), so in practice neither got the line. It is the narrowest area and therefore
the last one in the prompt.

The file names are German on purpose. They are notes a person opens in their own vault, and
renaming them would leave every insight learned so far behind in a file nothing reads.

The content is deliberately plain markdown, one bullet line per insight. There is no
parsing, there are no ids and no hit counters: the text is hung into the prompt as a block,
and merging duplicates is done by the agent itself over `forget` plus `remember`.

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
# that it does not cover the assignment. 6000 was too tight once the notes had grown — the
# person note alone filled it and the role note fell out of the prompt entirely, so the agent
# relearned rules it had long since written down (which is how the duplicates got in). 20000
# is roughly 6k tokens: person plus role fit whole today, and it sits at the top of the
# prompt, so the history cache carries it. `_fit` decides what gives when they grow past it.
MAX_MEMORY_CHARS = 20000
# A single insight is a sentence, not an essay.
MAX_ENTRY_CHARS = 600

AREAS = ("person", "agent", "project", "project_agent")

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
              "'project' = for this project, whichever role works on it · 'project_agent' = "
              "for your role in exactly this project. Take the narrowest area that is true: "
              "the narrower one weighs more when two memories disagree.")

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

# Writing into SOMEBODY ELSE'S memory. Deliberately not a fourth entry in `MEMORY_TOOLS`:
# those three are always allowed (`_ALWAYS_ALLOWED` in runtime.py) and are the only tools the
# look back after a run may call. A tool that reaches into a foreign note must be granted
# explicitly in `allowed_tools`, and it has no business in a look back at one's own run.
TEACH_TOOL_NAME = "memory_teach"
TEACH_TOOL = _def(
    TEACH_TOOL_NAME,
    "Write a lasting rule into the memory of ANOTHER agent. For what you noticed about "
    "somebody else's work over several runs — your own lessons belong in `remember`. One "
    "sentence per call, no reference to a single run.",
    {"agent": {"type": "string",
               "description": "The role whose memory is meant (developer, code_reviewer, "
                              "gameproj-operator …)."},
     "area": {"type": "string", "enum": ["agent", "project_agent"],
              "description": "'agent' = for that role everywhere · 'project_agent' = for that "
                             "role in the named project only."},
     "text": {"type": "string",
              "description": "The rule as one clear sentence, understandable on its own later."},
     "project": {"type": "string",
                 "description": "Project key — required for 'project_agent', ignored otherwise."}},
    ["agent", "area", "text"])


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
    if area == "project_agent":
        # Needs BOTH: without either half the note would silently widen to something the
        # insight was never meant for.
        if not (agent_role and project_key):
            return None
        return f"{root}/Projekt-{_safe(project_key)}-Agent-{_safe(agent_role)}.md"
    return None


async def memory_root(db: AsyncSession, owner_id: int | None) -> str:
    """The memory folder of the owner; empty means the feature is off."""
    if not owner_id:
        return ""
    user = await db.get(User, owner_id)
    return (user.vault_memory_path or "").strip() if user else ""


# MCP errors do NOT come as an exception: the server answers with a normal result that only
# carries an `isError` flag, and `mcp_client.call` throws that flag away. Whoever judges by
# the text alone reads an error out of a note that merely QUOTES one — on 2026-08-01 the line
# 'schlägt mit "Section target not found" fehl' landed in Agent-assistent.md, and from then on
# every read of that note counted as failed: the role memory silently fell out of the prompt
# and the curator skipped the note for weeks. That is why the flag is asked for here
# (`call_ex`) and the text is only a fallback for callers that cannot deliver it.
_ERROR_MARKER = ("mcp error", "kein mcp konfiguriert", "not found", "does not exist",
                  "file_exists", "error:", "\"code\":")


def _failed(text: str) -> bool:
    """Only for results without a flag: markers count at the START, not somewhere inside.

    An MCP error message begins with its cause ("Error: Not found: …"); a note begins with
    its own content. Searching the whole text would make every note that writes about errors
    unreadable.
    """
    low = (text or "").strip().lower()
    return not low or any(low.startswith(m) for m in _ERROR_MARKER)


async def _read_note(mcp, path: str) -> str:
    """Note content or empty (a missing note is the normal case, not the error case)."""
    try:
        if hasattr(mcp, "call_ex"):
            out, is_error = await mcp.call_ex(
                "obsidian__obsidian_get_note",
                {"format": "content", "target": _note_target(path)})
            if is_error:
                return ""
        else:
            out = await mcp.call("obsidian__obsidian_get_note",
                                 {"format": "content", "target": _note_target(path)})
            if _failed(out):
                return ""
    except Exception as exc:  # noqa: BLE001
        log.debug("Memory: %s not readable (%s)", path, exc)
        return ""
    return _strip_note_header(out, path)


def _strip_note_header(text: str, path: str) -> str:
    """Drop the `**<path>** (format: content)` line the obsidian MCP puts in front.

    It is presentation for a model reading a tool result, not part of the note. `forget`
    writes what it reads back into the note, so leaving it in would file the header away as
    the first line of the memory.
    """
    head = f"**{path}** (format: content)"
    body = text.lstrip()
    if body.startswith(head):
        body = body[len(head):]
    return body.lstrip("\n")


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
                        ("project", "For this project"),
                        ("project_agent", "For your role in this project")):
        path = note_path(root, area, agent_role, project_key)
        if not path:
            continue
        body = (await _read_note(mcp, path)).strip()
        if body:
            chunks.append(f"## {title}\n{body}")
    return _fit(chunks)


def _fit(chunks: list[str], budget: int = MAX_MEMORY_CHARS) -> str:
    """Join the blocks, shortening the GENERAL ones first when the budget is tight.

    A plain `[:MAX_MEMORY_CHARS]` over the joined text cuts from the end — and the end is the
    role and project memory, the most specific part and the one that weighs most in case of
    doubt. So hand the budget out from the back: the project block first, then the role, and
    whatever is left goes to the person block.
    """
    keep: list[str] = []
    left = budget
    for chunk in reversed(chunks):                      # specific before general
        if left <= 0:
            break
        if len(chunk) <= left:
            keep.append(chunk)
            left -= len(chunk) + 2                      # +2 for the blank line
        else:
            mark = "\n…(shortened)"
            keep.append(chunk[:max(0, left - len(mark))].rstrip() + mark)
            left = 0
    return "\n\n".join(reversed(keep))


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
        # `project_agent` can lack either half, so the message names what is actually missing
        # instead of guessing from the area alone.
        missing = []
        if area in ("agent", "project_agent") and not agent_role:
            missing.append("role")
        if area in ("project", "project_agent") and not project_key:
            missing.append("project")
        return (f"ERROR: the area '{area}' does not work in this run — there is no "
                f"{' and no '.join(missing) or 'context'}. Take 'person'.")

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



async def call_teach_tool(db: AsyncSession, mcp, owner_id: int | None, args: dict) -> str:
    """Write one line into the memory note of a different agent.

    Its own entry point rather than a fourth area of `call_memory_tool`: that one always
    writes into the role of the run it belongs to, which is exactly right for `remember` and
    exactly wrong here.
    """
    root = await memory_root(db, owner_id)
    if not root:
        return NO_MEMORY

    role = (args.get("agent") or "").strip()
    area = (args.get("area") or "").strip().lower()
    area = LEGACY_AREAS.get(area, area)
    project = (args.get("project") or "").strip()
    text = " ".join((args.get("text") or "").split())[:MAX_ENTRY_CHARS]
    if not role:
        return "ERROR: `agent` is missing — say whose memory is meant."
    if area not in ("agent", "project_agent"):
        return "ERROR: `area` has to be agent | project_agent."
    if not text:
        return "ERROR: `text` is missing."
    path = note_path(root, area, role, project)
    if not path:
        return "ERROR: the area 'project_agent' needs a `project` (its key, e.g. TRA)."

    today = dt.datetime.now().strftime("%Y-%m-%d")
    # The origin stays in the line. Whoever reads the note in the vault has to be able to see
    # that this rule came from the outside and not from a run of that agent itself; otherwise
    # a wrong judgement of the supervision looks like something the agent learned for itself.
    error = await _append_line(mcp, path, f"- [{today}] (Aufsicht) {text}")
    if error:
        return f"ERROR while teaching: {error}"
    return f"Noted in {path}."


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
