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

BEREICHE = ("mensch", "agent", "projekt")


def _def(name: str, desc: str, props: dict, required: list[str]) -> dict:
    return {"type": "function", "function": {
        "name": name, "description": desc,
        "parameters": {"type": "object", "properties": props, "required": required}}}


_BEREICH_DESC = ("Wohin die Erkenntnis gehört: 'mensch' = gilt immer und überall (Vorlieben, "
                 "Arbeitsweise, feste Vorgaben) · 'agent' = nur für deine Rolle · "
                 "'projekt' = nur für dieses Projekt.")

MEMORY_TOOLS = [
    _def("erinnere_dich",
         "Merke dir etwas DAUERHAFT für künftige Läufe — eine Vorgabe, Korrektur oder Vorliebe "
         "deines Menschen, die auch morgen noch gilt. Nicht für Tagesdetails, Ticket-Fakten oder "
         "Dinge, die schon im Gedächtnis stehen. Ein Satz pro Aufruf.",
         {"bereich": {"type": "string", "enum": list(BEREICHE), "description": _BEREICH_DESC},
          "text": {"type": "string", "description": "Die Erkenntnis als ein klarer Satz, so "
                                                    "formuliert, dass sie ohne den heutigen "
                                                    "Zusammenhang verständlich bleibt."}},
         ["bereich", "text"]),
    _def("vergiss",
         "Entferne eine Erinnerung, die überholt oder falsch ist. Nutze das auch, wenn dein "
         "Mensch eine frühere Vorgabe ändert: erst `vergiss`, dann `erinnere_dich` mit der neuen.",
         {"bereich": {"type": "string", "enum": list(BEREICHE), "description": _BEREICH_DESC},
          "textfragment": {"type": "string", "description": "Ein Stück der zu löschenden Zeile; "
                                                            "alle passenden Zeilen fallen weg."}},
         ["bereich", "textfragment"]),
    _def("gedaechtnis_suchen",
         "Durchsuche dein gesamtes Gedächtnis nach einem Stichwort. Nötig nur, wenn du etwas "
         "vermutest, das nicht im automatisch mitgelieferten Gedächtnis-Block steht.",
         {"suche": {"type": "string", "description": "Stichwort oder Wortgruppe."}},
         ["suche"]),
]
MEMORY_TOOL_NAMES = {t["function"]["name"] for t in MEMORY_TOOLS}

NO_MEMORY = "(kein Gedächtnis konfiguriert — dein Mensch hat keinen Vault-Ordner gesetzt)"


def _note_target(path: str) -> dict:
    """The `oneOf` address of the obsidian MCP. The ONLY place that knows its form.

    It has to be an object; a string here is exactly the error `MCP error -32602` older
    models fail on when they call the MCP themselves.
    """
    return {"type": "path", "path": path}


def _safe(part: str) -> str:
    """Role or project key as part of a file name: no path changes, no separators."""
    keep = [c for c in (part or "").strip() if c.isalnum() or c in "-_ äöüÄÖÜß"]
    return "".join(keep).strip() or "unbenannt"


def note_path(root: str, bereich: str, agent_role: str = "", project_key: str = "") -> str | None:
    """Note path for an area, or None when the area makes no sense here."""
    root = (root or "").strip().rstrip("/")
    if not root:
        return None
    if bereich == "mensch":
        return f"{root}/Mensch.md"
    if bereich == "agent":
        return f"{root}/Agent-{_safe(agent_role)}.md" if agent_role else None
    if bereich == "projekt":
        return f"{root}/Projekt-{_safe(project_key)}.md" if project_key else None
    return None


async def memory_root(db: AsyncSession, owner_id: int | None) -> str:
    """Gedächtnis-Ordner des Owners — leer heißt: Funktion aus."""
    if not owner_id:
        return ""
    user = await db.get(User, owner_id)
    return (user.vault_memory_path or "").strip() if user else ""


# MCP errors do NOT come as an exception: `mcp_client.call` discards `isError` and returns
# the error text of the server (mcp_client.py:97-103). A missing call success is therefore
# recognisable by the text here, and a "the note does not exist yet" is the normal case, not an error.
_FEHLER_MARKER = ("mcp error", "kein mcp konfiguriert", "not found", "does not exist",
                  "file_exists", "error:", "\"code\":")


def _failed(text: str) -> bool:
    low = (text or "").lower()
    return not text or any(m in low for m in _FEHLER_MARKER)


async def _read_note(mcp, path: str) -> str:
    """Note content or empty (a missing note is the normal case, not the error case)."""
    try:
        out = await mcp.call("obsidian__obsidian_get_note",
                             {"format": "content", "target": _note_target(path)})
    except Exception as exc:  # noqa: BLE001
        log.debug("Gedächtnis: %s nicht lesbar (%s)", path, exc)
        return ""
    return "" if _failed(out) else out


async def read_memory(mcp, root: str, agent_role: str = "", project_key: str = "") -> str:
    """The whole relevant memory as text for the prompt (truncated).

    The order goes from the general to the specific, so that the specific one stands at the
    end and weighs more in case of doubt.
    """
    if not root:
        return ""
    stuecke: list[str] = []
    for bereich, titel in (("mensch", "Über deinen Menschen"),
                           ("agent", "Für deine Rolle"),
                           ("projekt", "Für dieses Projekt")):
        path = note_path(root, bereich, agent_role, project_key)
        if not path:
            continue
        body = (await _read_note(mcp, path)).strip()
        if body:
            stuecke.append(f"## {titel}\n{body}")
    return "\n\n".join(stuecke)[:MAX_MEMORY_CHARS]


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
    kopf = f"# {path.rsplit('/', 1)[-1].removesuffix('.md')}\n\n"
    try:
        neu = await mcp.call("obsidian__obsidian_write_note",
                             {"target": _note_target(path), "content": kopf + line + "\n"})
        if not _failed(neu):
            return ""
        return neu
    except Exception as exc:  # noqa: BLE001
        return f"{out} / {exc}"


async def call_memory_tool(db: AsyncSession, mcp, owner_id: int | None, name: str, args: dict,
                           agent_role: str = "", project_key: str = "") -> str:
    """Dispatcher for the three memory tools. The return value is terse text for the agent."""
    root = await memory_root(db, owner_id)
    if not root:
        return NO_MEMORY

    if name == "gedaechtnis_suchen":
        suche = (args.get("suche") or "").strip()
        if not suche:
            return "FEHLER: `suche` fehlt."
        try:
            out = await mcp.call("obsidian__obsidian_search_notes",
                                 {"mode": "text", "query": suche, "pathPrefix": root})
        except Exception as exc:  # noqa: BLE001
            return f"FEHLER bei der Suche: {exc}"
        return (out or "Nichts gefunden.")[:4000]

    bereich = (args.get("bereich") or "").strip().lower()
    if bereich not in BEREICHE:
        return f"FEHLER: `bereich` muss {' | '.join(BEREICHE)} sein."
    path = note_path(root, bereich, agent_role, project_key)
    if not path:
        fehlt = "Projekt" if bereich == "projekt" else "Rolle"
        return (f"FEHLER: Bereich '{bereich}' geht in diesem Lauf nicht — es gibt kein {fehlt}. "
                "Nimm 'mensch'.")

    if name == "erinnere_dich":
        text = " ".join((args.get("text") or "").split())[:MAX_ENTRY_CHARS]
        if not text:
            return "FEHLER: `text` fehlt."
        heute = dt.datetime.now().strftime("%Y-%m-%d")
        fehler = await _append_line(mcp, path, f"- [{heute}] {text}")
        if fehler:
            return f"FEHLER beim Merken: {fehler}"
        return f"Gemerkt in {path}."

    if name == "vergiss":
        frag = (args.get("textfragment") or "").strip().lower()
        if not frag:
            return "FEHLER: `textfragment` fehlt."
        body = await _read_note(mcp, path)
        if not body:
            return f"Nichts zu vergessen — {path} ist leer oder gibt es nicht."
        behalten = [ln for ln in body.splitlines() if frag not in ln.lower()]
        weg = len(body.splitlines()) - len(behalten)
        if not weg:
            return f"Keine Zeile in {path} enthält '{frag}' — nichts geändert."
        try:
            out = await mcp.call("obsidian__obsidian_write_note",
                                 {"target": _note_target(path), "overwrite": True,
                                  "content": "\n".join(behalten).rstrip() + "\n"})
        except Exception as exc:  # noqa: BLE001
            return f"FEHLER beim Vergessen: {exc}"
        if _failed(out):
            return f"FEHLER beim Vergessen: {out}"
        return f"{weg} Zeile(n) aus {path} entfernt."

    return f"FEHLER: unbekanntes Gedächtnis-Tool '{name}'."


REFLEXION_PROMPT = (
    "Rückschau auf diesen Lauf. Gab es eine Korrektur, Vorgabe oder Vorliebe deines Menschen, "
    "die AUCH FÜR KÜNFTIGE LÄUFE gilt? Merke sie mit `erinnere_dich` im passenden Bereich — "
    "einen Satz pro Aufruf, ohne Bezug auf das heutige Ticket, damit sie später allein "
    "verständlich ist.\n\n"
    "Merke NICHT: Tagesdetails, Ticket-Fakten, Zwischenstände, technische Einzelheiten dieses "
    "Auftrags, und nichts, was oben schon im Gedächtnis-Block steht. Ist eine dort stehende "
    "Erinnerung durch heute überholt, korrigiere sie (`vergiss`, dann `erinnere_dich`).\n\n"
    "Hast du nichts Dauerhaftes gelernt — der Normalfall — dann rufe KEIN Tool und antworte "
    "nur mit „nichts\"."
)
