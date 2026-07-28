"""Gedächtnis der Agenten: gelernte Erkenntnisse als Notizen im Obsidian-Vault (TRA-30).

Der Ablageort ist der Vault, weil der Mensch das Gelernte sehen und von Hand korrigieren
können soll — eine DB-Tabelle wäre unsichtbar. Unter `users.vault_memory_path` liegen drei
Sorten Notizen:

    Mensch.md            gilt für ALLE Läufe dieses Menschen (Vorlieben, feste Vorgaben)
    Agent-<rolle>.md     rollenspezifisch (assistent, developer, code_reviewer …)
    Projekt-<KEY>.md     projektspezifisch

Der Inhalt ist absichtlich schlichtes Markdown, eine Bullet-Zeile pro Erkenntnis. Es gibt
kein Parsing, keine IDs, keine Trefferzähler: der Text wird als Block in den Prompt gehängt,
und das Zusammenfassen von Dubletten macht der Agent selbst über `vergiss` + `erinnere_dich`.

WARUM DIESE TOOLS ÜBERHAUPT EXISTIEREN — der obsidian-MCP beschreibt `target` als `oneOf`
ohne `type`-Feld. Modelle wie `claude-sonnet-4-5` bedienen das nicht: sie schicken `target`
als JSON-String statt als Objekt, jeder Aufruf endet in `MCP error -32602`. Deshalb ruft das
Modell obsidian hier NIE selbst auf. Es bekommt Tools mit reinen String-Parametern, und
`_note_target` unten ist die einzige Stelle im Haus, die die `oneOf`-Form kennt. Damit läuft
das Gedächtnis auf jedem Modell.
"""
from __future__ import annotations

import datetime as dt
import logging

from sqlalchemy.ext.asyncio import AsyncSession

from ..models.user import User

log = logging.getLogger(__name__)

# Wie viel Gedächtnis höchstens in den Prompt geht — genug für ein paar Dutzend Zeilen,
# wenig genug, dass es den Auftrag nicht zudeckt.
MAX_MEMORY_CHARS = 6000
# Eine einzelne Erkenntnis ist ein Satz, kein Aufsatz.
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
    """Die `oneOf`-Adresse des obsidian-MCP. EINZIGE Stelle, die deren Form kennt.

    Muss ein Objekt sein — ein String hier ist genau der Fehler `MCP error -32602`, an dem
    ältere Modelle scheitern, wenn sie den MCP selbst aufrufen.
    """
    return {"type": "path", "path": path}


def _safe(part: str) -> str:
    """Rolle/Projekt-Key als Dateinamens-Bestandteil: keine Pfadwechsel, keine Trenner."""
    keep = [c for c in (part or "").strip() if c.isalnum() or c in "-_ äöüÄÖÜß"]
    return "".join(keep).strip() or "unbenannt"


def note_path(root: str, bereich: str, agent_role: str = "", project_key: str = "") -> str | None:
    """Notizpfad für einen Bereich — None, wenn der Bereich hier keinen Sinn hat."""
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


# MCP-Fehler kommen NICHT als Ausnahme: `mcp_client.call` verwirft `isError` und gibt den
# Fehlertext des Servers zurück (mcp_client.py:97-103). Ein fehlender Aufruf-Erfolg ist hier
# also am Text zu erkennen — und ein „Notiz gibt es noch nicht" ist der Normalfall, kein Fehler.
_FEHLER_MARKER = ("mcp error", "kein mcp konfiguriert", "not found", "does not exist",
                  "file_exists", "error:", "\"code\":")


def _failed(text: str) -> bool:
    low = (text or "").lower()
    return not text or any(m in low for m in _FEHLER_MARKER)


async def _read_note(mcp, path: str) -> str:
    """Notizinhalt oder leer (fehlende Notiz ist der Normalfall, nicht der Fehlerfall)."""
    try:
        out = await mcp.call("obsidian__obsidian_get_note",
                             {"format": "content", "target": _note_target(path)})
    except Exception as exc:  # noqa: BLE001
        log.debug("Gedächtnis: %s nicht lesbar (%s)", path, exc)
        return ""
    return "" if _failed(out) else out


async def read_memory(mcp, root: str, agent_role: str = "", project_key: str = "") -> str:
    """Gesamtes einschlägiges Gedächtnis als Text für den Prompt (gekappt).

    Reihenfolge vom Allgemeinen zum Besonderen, damit das Spezifische am Ende steht und
    im Zweifel schwerer wiegt.
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
    """Zeile anhängen; existiert die Notiz noch nicht, einmal anlegen."""
    try:
        out = await mcp.call("obsidian__obsidian_append_to_note",
                             {"target": _note_target(path), "content": line + "\n"})
        if not _failed(out):
            return ""
    except Exception as exc:  # noqa: BLE001
        out = str(exc)
    # Zweiter Versuch: Notiz anlegen. `overwrite` bleibt aus — gibt es sie doch schon,
    # scheitert der Aufruf lieber, als bestehendes Gedächtnis zu überschreiben.
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
    """Dispatcher für die drei Gedächtnis-Tools. Rückgabe = knapper Text für den Agenten."""
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
