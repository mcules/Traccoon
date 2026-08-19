"""Agent tool loop (ported from the predecessor, agent/runtime.py, onto SQLAlchemy).

mode=plan|execute. Eingebaute Tools: fs_read/list/write/edit, check, deploy,
screenshot, ask_human, submit_plan, continue_later, open_tasks, delegate.
Runtime permission gate, build gate and max_iterations behave as in the predecessor.
"""
from __future__ import annotations

import asyncio
import base64
import datetime as dt
import json
import logging
import os
from dataclasses import dataclass
from typing import Any

import httpx
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..models.agents import AgentDefinition, Run
from ..models.assistant import AssistantTask
from ..models.ticket import Blocker, Comment
from ..services import office
from . import codegraph as _codegraph
from . import gitops as _gitops
from . import perms
from .mcp_client import mcp_session
from .providers.base import ProviderError
from .providers.router import router
from .assistant_gate import gate_check
from .tools_memory import (
    MEMORY_TOOL_NAMES, MEMORY_TOOLS, REFLEXION_PROMPT, call_memory_tool, memory_root, read_memory,
)
from .compaction import kompaktiere as _kompaktiere
from .compaction import uebergabe as _uebergabe
from .tools_traccoon import (
    TRACCOON_GATED_TOOLS,
    TRACCOON_TOOL_NAMES,
    TRACCOON_TOOLS,
    call_traccoon_tool,
)

log = logging.getLogger("traccoon.runtime")

_FS_MAX_READ = 30000
MAX_BUILD_GATE = 3
MAX_DELEGATION_DEPTH = 2
# Hard input token budget per run: keeps a single run from blowing up context and spend
# through the quadratically growing message history. On overrun the run ends like it does on
# the iteration limit, as loop_exhausted.
# finalisiert (Continuation greift in frischem Run; Per-Ticket-Cap deckelt gesamt).
MAX_RUN_INPUT_TOKENS = int(os.getenv("MAX_RUN_INPUT_TOKENS", "2000000"))
# Wall clock limit per run. The loop watchdog in the worker only sees a BLOCKED event loop;
# an agent that cheerfully keeps calling tools and still never finishes ticks along fine and
# used to run unbounded (`run_timeout` applies to shell and HTTP jobs in the scheduler only).
# It ends like the iteration limit: loop_exhausted, continuation in a fresh run, and the caps
# bound the whole thing. 0 switches the limit off.
MAX_RUN_SECONDS = float(os.getenv("AGENT_RUN_TIMEOUT_SEC", "1800"))

# Upper bound for answers of `traccoon_http_call`. The real limit is set by the destination
# (Destination.max_response_chars); this is only the bar against a misconfigured destination
# flooding a whole run context.
MAX_HTTP_TOOL_CHARS = int(os.getenv("MAX_HTTP_TOOL_CHARS", "60000"))

DEPLOYER_URL = os.getenv("DEPLOYER_URL", "http://deployer:8661")
SHOTTER_URL = os.getenv("SHOTTER_URL", "http://shotter:8700")


def _now() -> dt.datetime:
    return dt.datetime.now(tz=dt.timezone.utc)


# ---------- Tool-Schemas (Port) ----------

# Loop and control tools are agent mechanics, not bounded by the allowlist (ALWAYS available).
# `traccoon_notify_human` belongs to them: since the assistant only reports when it explicitly
# decides to, a missing allowlist entry would mean "never reports", and important things would
# stay silent too. The tool writes a message to the agent's own person and attacks nothing.

# The memory tools belong here as well: `allowed_tools` is deny by default, so a missing entry
# would mean "silently never learns", exactly the state TRA-30 ended. They write only into the
# memory folder of the agent's own person.
_ALWAYS_ALLOWED = {"ask_human", "continue_later", "open_tasks", "load_skill", "submit_plan",
                   "delegate", "traccoon_notify_human"} | MEMORY_TOOL_NAMES

SUBMIT_PLAN_TOOL = {"type": "function", "function": {
    "name": "submit_plan",
    "description": "Reiche den fertigen Umsetzungsplan (Markdown) ein. Beendet die Planungsphase "
                   "– der Plan geht zur Freigabe an den Menschen.",
    "parameters": {"type": "object", "properties": {
        "plan": {"type": "string", "description": "Vollständiger Plan in Markdown"},
        "summary": {"type": "string", "description": "1–3 Sätze: was wurde geplant?"}},
        "required": ["plan"]}}}

ASK_HUMAN_TOOL = {"type": "function", "function": {
    "name": "ask_human",
    "description": "Stelle dem Menschen eine Rückfrage, wenn du blockiert bist und ohne Antwort nicht "
                   "weiterarbeiten kannst. Beendet den Lauf bis zur Antwort. Nur bei echten Blockern.",
    "parameters": {"type": "object", "properties": {"question": {"type": "string"}}, "required": ["question"]}}}

CONTINUE_LATER_TOOL = {"type": "function", "function": {
    "name": "continue_later",
    "description": "Signalisiere, dass du noch eine Runde brauchst. Traccoon startet automatisch eine "
                   "Fortsetzung mit demselben Worktree-Stand. NIEMALS zusammen mit ask_human.",
    "parameters": {"type": "object", "properties": {"summary": {"type": "string"}}, "required": ["summary"]}}}

FS_READ_TOOL = {"type": "function", "function": {
    "name": "fs_read",
    "description": "Datei im Projekt-Workspace lesen (mit Zeilennummern). `path` relativ zur Projektwurzel. "
                   "Große Dateien seitenweise via `offset`/`limit`.",
    "parameters": {"type": "object", "properties": {
        "path": {"type": "string"}, "offset": {"type": "integer"}, "limit": {"type": "integer"}},
        "required": ["path"]}}}
FS_LIST_TOOL = {"type": "function", "function": {
    "name": "fs_list", "description": "Dateibaum im Projekt-Workspace auflisten.",
    "parameters": {"type": "object", "properties": {"path": {"type": "string"}}, "required": []}}}
FS_WRITE_TOOL = {"type": "function", "function": {
    "name": "fs_write", "description": "Datei schreiben/überschreiben (legt Verzeichnisse an).",
    "parameters": {"type": "object", "properties": {
        "path": {"type": "string"}, "content": {"type": "string"}}, "required": ["path", "content"]}}}
FS_EDIT_TOOL = {"type": "function", "function": {
    "name": "fs_edit",
    "description": "Textstelle ersetzen. `old` muss EXAKT der Dateiinhalt sein (OHNE Zeilennummern-Präfix). "
                   "Alle Vorkommen von `old` werden durch `new` ersetzt.",
    "parameters": {"type": "object", "properties": {
        "path": {"type": "string"}, "old": {"type": "string"}, "new": {"type": "string"}},
        "required": ["path", "old", "new"]}}}
CHECK_TOOL = {"type": "function", "function": {
    "name": "check", "description": "Baut/prüft das Projekt OHNE Deploy (verify_command). Rufe dies nach "
    "fs_write/fs_edit und behebe alle Fehler, BEVOR du `deploy` rufst.",
    "parameters": {"type": "object", "properties": {}}}}
DEPLOY_TOOL = {"type": "function", "function": {
    "name": "deploy", "description": "Baut das Projekt neu und startet es (via Deployer). Wartet auf Ergebnis.",
    "parameters": {"type": "object", "properties": {}}}}
SCREENSHOT_TOOL = {"type": "function", "function": {
    "name": "screenshot", "description": "Screenshot der gerenderten Projekt-Seite (Vision) zur UI-Kontrolle. "
    "`target` = Hash-Route, z.B. 'q/NA101' oder leer.",
    "parameters": {"type": "object", "properties": {"target": {"type": "string"}}, "required": []}}}
READ_ATTACHMENT_TOOL = {"type": "function", "function": {
    "name": "read_attachment", "description": "Liest einen an DIESES Ticket angehängten Anhang. "
    "Bilder/Screenshots werden dir als Bild gezeigt (Vision), Text-Dateien als Text. "
    "`name` = Dateiname des Anhangs (siehe die Anhang-Liste im Auftrag).",
    "parameters": {"type": "object", "properties": {"name": {"type": "string", "description": "Dateiname des Anhangs"}},
                   "required": ["name"]}}}
OPEN_TASKS_TOOL = {"type": "function", "function": {
    "name": "open_tasks", "description": "Offene, einem Agenten zugewiesene Tickets (read-only).",
    "parameters": {"type": "object", "properties": {}}}}
CODEGRAPH_TOOL = {"type": "function", "function": {
    "name": "codegraph",
    "description": (
        "Code-Wissensgraph dieses Projekts abfragen — nutze das ZUERST, um Symbole, Aufrufwege und "
        "Blast-Radius zu verstehen, statt viele Dateien einzeln zu lesen (spart Tokens). `explore` gibt "
        "die relevanten Symbol-Quellen VERBATIM zurück; dort gezeigte Dateien musst du NICHT nochmal "
        "fs_read. commands: explore=<Frage> (relevante Quellen+Aufrufpfade+Blast-Radius in einem Schuss), "
        "query=<Symbolsuche>, node=<Symbol|Datei>, callers=<Symbol>, callees=<Symbol>, "
        "impact=<Symbol> (Was bricht bei Änderung?), files=<Pfad> (Struktur), affected=<Datei> (betroffene Tests)."),
    "parameters": {"type": "object", "properties": {
        "command": {"type": "string",
                    "enum": ["explore", "query", "node", "callers", "callees", "impact", "files", "affected"]},
        "query": {"type": "string", "description": "Frage/Symbol/Datei/Pfad je nach command"}},
        "required": ["command", "query"]}}}


def _delegate_tool(roles: list[str]) -> dict:
    return {"type": "function", "function": {
        "name": "delegate",
        "description": "Delegiere eine Teilaufgabe an einen spezialisierten Sub-Agenten (läuft im selben "
                       "Workspace). Nutze das, wenn eine andere Rolle besser passt. Gib die Rolle + eine "
                       "klare Teilaufgabe an; du erhältst das Ergebnis zurück.",
        "parameters": {"type": "object", "properties": {
            "role": {"type": "string", "enum": roles or None, "description": "Ziel-Rolle"},
            "task": {"type": "string", "description": "Klare Teilaufgabe für den Sub-Agenten"}},
            "required": ["role", "task"]}}}

CODE_WORKFLOW = (
    "## Code-Workflow (Projekt-Workspace)\n"
    "0. Lesen ist Mittel, nicht Zweck. Verschaffe dir einen Überblick und fang dann mit der KLEINSTEN "
    "sinnvollen Änderung an — weiterlesen kannst du danach jederzeit, und eine begonnene Änderung "
    "überlebt das Ende des Laufs, reine Leserei nicht. Wer eine halbe Stunde nur liest, hat nichts "
    "geliefert.\n"
    "1. Verstehe den Code BEVOR du änderst — auch ähnliche Stellen, damit Änderungen KONSISTENT sind. "
    "Nutze wenn verfügbar ZUERST `codegraph` (explore/impact) für Symbole, Aufrufwege & Blast-Radius — das "
    "spart viele fs_read; erst danach fs_read/fs_list für Details.\n2. Ändere mit fs_write/fs_edit — "
    "CHIRURGISCH. LÖSCHE NIEMALS große Blöcke/Funktionen "
    "nur, damit ein Fehler verschwindet; behebe die Ursache.\n3. Rufe nach JEDER Änderung `check` und behebe "
    "die Fehler, bis der Build GRÜN ist.\n4. ERST bei grünem Build: `deploy`.\n"
    "Bei hartnäckigem Build-Fehler (2-3 rote checks): `ask_human` statt blind weiter.\n"
    "Bei UI-Änderungen: bestehendes Element ändern statt duplizieren, nach Edit erneut lesen; wenn `screenshot` "
    "verfügbar, nach dem Deploy screenshotten und das Bild selbst prüfen; sonst um Sichtprüfung bitten."
)

FS_TOOL_NAMES = {"fs_read", "fs_list", "fs_write", "fs_edit"}
# How a run delivers its result. Everything else is preparation: useful, but gone after the
# run ends (the worktree survives, the conversation does not).
ERGEBNIS_TOOLS = {"execute": {"fs_write", "fs_edit"}, "plan": {"submit_plan"}}
# After which share of the iteration or time budget without a result a reminder follows.
# Twice, then enough: one reminder gets overlooked, three are nagging.
ERMAHNUNG_BEI = (0.35, 0.65)


def ermahnungen_faellig(verbraucht: float, bereits: int) -> int:
    """How many reminders should have been given at this point of the budget."""
    n = bereits
    while n < len(ERMAHNUNG_BEI) and verbraucht >= ERMAHNUNG_BEI[n]:
        n += 1
    return n


def ermahnung_text(mode: str, verbraucht: float, scharf: bool) -> str:
    """Remind a run that spends its budget without delivering anything.

    The tone rises once: first a request to check, then a demand. Both name the same point:
    research does not survive the end of the run, a change in the worktree does. The single
    reminder that existed before came at round 78 of 80 and was therefore only a message to
    the executor of the estate.
    """
    if mode == "plan":
        was = "noch keinen Plan eingereicht"
        bleibt = "ein eingereichter Plan schon"
        nachsatz = ("Reiche JETZT einen Plan ein (`submit_plan`), auch wenn Details offen "
                    "sind — benenne die offenen Punkte darin. Nur bei echter Blockade: "
                    "`ask_human`."
                    if scharf else
                    "Prüfe, ob du genug weißt, um den Plan zu schreiben — im Zweifel ja: "
                    "ein Plan mit benannten Unsicherheiten ist mehr wert als keiner.")
    else:
        was = "noch keine Änderung geschrieben"
        bleibt = "eine begonnene Änderung schon"
        nachsatz = ("Fang JETZT mit der kleinsten sinnvollen Änderung an, statt weiterzulesen. "
                    "Fehlt dir eine Entscheidung, die du nicht selbst treffen kannst: "
                    "`ask_human`. Kannst du wirklich nur übergeben: `continue_later` mit "
                    "allem, was du weißt."
                    if scharf else
                    "Prüfe, ob du genug weißt, um anzufangen — im Zweifel ja: die kleinste "
                    "sinnvolle Änderung zuerst, den Rest danach.")
    return (f"⚠️ {int(verbraucht * 100)} % deines Budgets für diesen Lauf sind weg und du hast "
            f"{was}. Recherche allein überlebt das Ende des Laufs nicht — "
            f"{bleibt}.\n{nachsatz}")


# ---------- FS-Tools ----------

def _fs_resolve(root: str, rel: str) -> str:
    full = os.path.realpath(os.path.join(root, rel or "."))
    rootr = os.path.realpath(root)
    if full != rootr and not full.startswith(rootr + os.sep):
        raise ValueError("Path outside the project workspace")
    return full


def _fs_dispatch(name: str, root: str | None, args: dict[str, Any]) -> str:
    if not root:
        return "FEHLER: kein bearbeitbarer Workspace für dieses Projekt."
    try:
        if name == "fs_read":
            with open(_fs_resolve(root, args.get("path", "")), encoding="utf-8", errors="replace") as f:
                lines = f.read().splitlines()
            total = len(lines)
            offset = max(0, int(args.get("offset") or 1) - 1)
            limit = int(args.get("limit") or 0)
            sel = lines[offset:offset + limit] if limit > 0 else lines[offset:]
            out: list[str] = []
            chars = 0
            next_line = None
            for i, ln in enumerate(sel, start=offset + 1):
                row = f"{i}\t{ln}"
                if chars + len(row) + 1 > _FS_MAX_READ:
                    next_line = i
                    break
                out.append(row)
                chars += len(row) + 1
            head = f"[{args.get('path')} · {total} Zeilen total · zeige {offset + 1}–{offset + len(out)}]"
            body = "\n".join(out) if out else "(leer / Bereich außerhalb der Datei)"
            tail = (f"\n…(gekürzt — weiterlesen mit offset={next_line})" if next_line else "")
            return head + "\n" + body + tail
        if name == "fs_list":
            base = _fs_resolve(root, args.get("path", "."))
            out2: list[str] = []
            for dirpath, dirnames, files in os.walk(base):
                dirnames[:] = [d for d in dirnames if d not in (".git", "__pycache__", "node_modules", ".venv")]
                depth = dirpath[len(base):].count(os.sep)
                if depth > 3:
                    dirnames[:] = []
                    continue
                rel = os.path.relpath(dirpath, root)
                for fn in sorted(files):
                    out2.append(os.path.join(rel, fn) if rel != "." else fn)
                if len(out2) > 800:
                    out2.append("…(gekürzt)")
                    break
            return "\n".join(out2) or "(leer)"
        if name == "fs_write":
            full = _fs_resolve(root, args.get("path", ""))
            content = args.get("content", "")
            os.makedirs(os.path.dirname(full), exist_ok=True)
            with open(full, "w", encoding="utf-8") as f:
                f.write(content)
            return f"OK: {len(content)} Zeichen nach {args.get('path')} geschrieben."
        if name == "fs_edit":
            full = _fs_resolve(root, args.get("path", ""))
            old, new = args.get("old", ""), args.get("new", "")
            if not old:
                return "FEHLER: `old` ist leer."
            with open(full, encoding="utf-8") as f:
                text = f.read()
            n = text.count(old)
            if n == 0:
                return "FEHLER: `old` kommt in der Datei nicht vor (exakt prüfen)."
            with open(full, "w", encoding="utf-8") as f:
                f.write(text.replace(old, new))
            return f"OK: {n} Ersetzung(en) in {args.get('path')}."
    except FileNotFoundError:
        return f"FEHLER: Datei nicht gefunden: {args.get('path')}"
    except ValueError as exc:
        return f"FEHLER: {exc}"
    except Exception as exc:  # noqa: BLE001
        return f"FS-FEHLER: {exc}"
    return f"FEHLER: unbekanntes FS-Tool {name}"


async def _do_check(ws_root: str | None, verify_command: str) -> str:
    if not ws_root:
        return "FEHLER: kein Workspace."
    if not verify_command:
        return "✅ BUILD OK (kein verify_command gesetzt — kein Build-Check konfiguriert)."
    try:
        p = await asyncio.create_subprocess_shell(
            verify_command, cwd=ws_root, stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT)
        out, _ = await asyncio.wait_for(p.communicate(), timeout=600)
        text = out.decode("utf-8", "replace")
        if (p.returncode or 0) == 0:
            return "✅ BUILD OK\n" + text[-1500:]
        return "❌ BUILD FEHLGESCHLAGEN\n" + text[-4000:]
    except asyncio.TimeoutError:
        return "❌ BUILD TIMEOUT (>600s)"
    except Exception as exc:  # noqa: BLE001
        return f"❌ CHECK-FEHLER: {exc}"


def deploy_gesperrt(stack_dir: str) -> str:
    """Why a deploy is not even queued here, empty when it is possible.

    The deployer rejects a request without a stack directory of its own on principle (an
    implicit host deploy is locked, otherwise Traccoon would recreate itself in the middle of
    a run). Until now only it noticed: the agent created a deployment row, waited in three
    second beats and got a refusal after the detour. **56 of the 186 rows in `deployments`
    are exactly this refusal**, and in every one of those runs an agent spent a turn on it.
    Whoever knows the answer already should give it right away.
    """
    selbst = (os.getenv("SELF_STACK_DIR") or "").rstrip("/")
    ziel = (stack_dir or "").rstrip("/")
    if not ziel:
        return ("Dieses Projekt hat kein eigenes Stack-Verzeichnis — ein Deploy ist hier "
                "nicht vorgesehen und würde abgelehnt. Prüfe deine Änderung mit `check`; "
                "live geht sie über Abnahme und Merge, das Wartungs-Update löst ein Mensch aus.")
    if selbst and ziel == selbst:
        return ("Das Ziel ist Traccoon selbst. Ein Self-Deploy läuft ausschließlich über das "
                "explizite Wartungs-Update (ein Mensch löst es aus) — er würde den eigenen "
                "Lauf mitten im Arbeiten neu starten. Prüfe mit `check` und schließe ab.")
    return ""


async def _do_deploy(db: AsyncSession, issue_id: int, project_id: int, stack_dir: str,
                     worktree: str | None, check_only: bool = False) -> str:
    """Queue a deployment (deployments table) and wait for the result of the deployer sidecar."""
    if not check_only and (grund := deploy_gesperrt(stack_dir)):
        return f"❌ NICHT MÖGLICH\n{grund}"
    from ..models.ops import Deployment
    dep = Deployment(issue_id=issue_id, project_id=project_id, stack_dir=stack_dir,
                     worktree=worktree or "", check_only=check_only, source="agent",
                     status="pending-check" if check_only else "pending")
    db.add(dep)
    await db.commit()
    await db.refresh(dep)
    deadline = 240 if not check_only else 200
    waited = 0
    while waited < deadline:
        await asyncio.sleep(3)
        waited += 3
        await db.refresh(dep)
        if dep.status in ("ok", "failed", "rolledback"):
            head = "✅ OK" if dep.status == "ok" else ("❌ FEHLGESCHLAGEN" if dep.status == "failed" else "↩ ROLLBACK")
            return f"{head}\n{(dep.log or '')[-3000:]}"
    return "FEHLER: Deployer-Timeout (läuft der deployer-Sidecar?)."


async def _do_screenshot(args: dict[str, Any], base_url: str) -> Any:
    target = (args.get("target") or "").strip()
    try:
        async with httpx.AsyncClient(timeout=150) as client:
            r = await client.post(f"{SHOTTER_URL.rstrip('/')}/shot",
                                  json={"target": target, "base_url": base_url})
    except Exception as exc:  # noqa: BLE001
        return f"FEHLER: Screenshot-Dienst nicht erreichbar: {exc}"
    if r.status_code != 200 or not r.headers.get("content-type", "").startswith("image"):
        return f"FEHLER beim Screenshot (HTTP {r.status_code}): {r.text[:200]}"
    b64 = base64.b64encode(r.content).decode()
    return [
        {"type": "image", "source": {"type": "base64", "media_type": "image/png", "data": b64}},
        {"type": "text", "text": f"Screenshot #{target or '(Startseite)'}. Prüfe Position/Größe/Dubletten."},
    ]


_IMG_MEDIA = {"image/png", "image/jpeg", "image/gif", "image/webp"}
_TEXT_EXT = (".txt", ".md", ".log", ".json", ".csv", ".yaml", ".yml", ".xml", ".ini", ".toml", ".env")


async def _do_read_attachment(db: AsyncSession, issue_id: int, args: dict[str, Any]) -> Any:
    """Reads a ticket attachment: images as a vision block, text as text, otherwise a note."""
    from ..models.ticket import Attachment
    name = (args.get("name") or "").strip()
    rows = (await db.execute(
        select(Attachment).where(Attachment.issue_id == issue_id).order_by(Attachment.id)
    )).scalars().all()
    if not rows:
        return "Dieses Ticket hat keine Anhänge."
    att = (next((a for a in rows if a.filename == name), None)
           or next((a for a in rows if name and name.lower() in a.filename.lower()), None))
    if att is None:
        return f"Anhang '{name}' nicht gefunden. Verfügbar: {', '.join(a.filename for a in rows)}"
    mime = (att.mime_type or "application/octet-stream").lower()
    data = att.data or b""
    if mime.startswith("image/"):
        media = mime if mime in _IMG_MEDIA else "image/png"
        return [
            {"type": "image", "source": {"type": "base64", "media_type": media,
                                         "data": base64.b64encode(data).decode()}},
            {"type": "text", "text": f"Anhang „{att.filename}“ ({mime}, {len(data)} Bytes)."},
        ]
    if mime.startswith("text/") or mime in ("application/json", "application/xml", "application/x-yaml") \
            or att.filename.lower().endswith(_TEXT_EXT):
        return f"Anhang „{att.filename}“ ({mime}):\n\n{data.decode('utf-8', errors='replace')[:16000]}"
    return f"Anhang „{att.filename}“ ist eine Binärdatei ({mime}, {len(data)} Bytes) — nicht als Text/Bild lesbar."


# ---------- Agent-Definition ----------

@dataclass
class AgentDef:
    id: int | None
    name: str
    role: str
    system_prompt: str
    provider: str
    model: str
    token_name: str
    fallback: str | None
    fallback_model: str
    fallback_token_name: str
    temperature: float
    max_tokens: int
    max_iterations: int
    can_code: bool
    can_read_code: bool
    can_delegate: bool
    web_search: bool
    allowed_tools: list[str]
    allowed_skills: list[str]
    autoload_skills: list[str]
    delegate_to: list[str]
    # Reads the memory at the start and looks back after the run (TRA-30). On by default;
    # without a vault folder set on the owner nothing happens anyway.
    learns: bool = True
    # Threshold for compacting the history (worker/compaction.py). None means off. It was
    # missing here although the run reads the value, so every run that reached the place
    # starb an AttributeError.
    max_context_tokens: int | None = None
    # Thinking depth (low|medium|high|xhigh|max, empty means the provider default `high`).
    # Thinking shares `max_tokens` with the visible answer, so whoever has much to read but
    # little to write (the reviewer) is safer with a lower level.
    effort: str = ""

    def tool_allowed(self, name: str) -> bool:
        # Loop and control tools are agent mechanics, not bounded by the allowlist.
        if name in _ALWAYS_ALLOWED:
            return True
        # Capability and tool server tools: ONLY when explicitly allowed by allowed_tools (glob).
        # Leere Liste = nichts (deny-by-default). MCP-Server via "server__*".
        from fnmatch import fnmatch
        return any(fnmatch(name, p) for p in (self.allowed_tools or []))


def agent_def_from_row(row: AgentDefinition, mode: str) -> AgentDef:
    return AgentDef(
        id=row.id, name=row.role, role=row.role, system_prompt=row.system_prompt,
        provider=row.provider, model=row.model, token_name=row.token_name or "",
        fallback=row.fallback, fallback_model=row.fallback_model or "",
        fallback_token_name=row.fallback_token_name or "",
        temperature=row.temperature, max_tokens=row.max_tokens,
        max_iterations=row.max_turns_planning if mode == "plan" else row.max_turns_execution,
        can_code=row.can_code, can_read_code=row.can_read_code, can_delegate=row.can_delegate,
        web_search=row.web_search, allowed_tools=list(row.allowed_tools or []),
        allowed_skills=list(row.allowed_skills or []),
        autoload_skills=list(row.autoload_skills or []), delegate_to=list(row.delegate_to or []),
        learns=bool(row.learns), max_context_tokens=row.max_context_tokens,
        effort=(row.effort or "").strip(),
    )


@dataclass
class RunResult:
    status: str          # done | blocked | failed | planned | loop_exhausted
    text: str = ""
    iterations: int = 0
    summary: str = ""
    run_id: int | None = None
    blocker_kind: str | None = None   # ask_human | permission


# ---------- DB-Helfer ----------

async def _start_run(db: AsyncSession, issue_id: int, agent: str, phase: str, provider: str,
                     model: str, parent_run_id: int | None, continuation_index: int,
                     task_id: str = "", *, project_id: int | None = None,
                     owner_id: int | None = None, parent_tool_use_id: str | None = None,
                     spawn_depth: int = 0) -> Run:
    # The task_id MUST be exactly the one the worker writes result:{task_id} under and the
    # dispatcher checks with wait_result/peek_result, otherwise the reattach correlation breaks
    # (recover_on_start reads run.task_id to bind a running worker run again after a backend
    # reload instead of orphaning it).
    # Project and owner additionally hang on the run so the office can authorise every event
    # without asking the ticket; projectless runs (job, assistant) would have nothing to fetch
    # there anyway. The whole run is returned instead of only the id: the caller builds the
    # `RunCtx` from it, and loading it a second time would only be a second truth.
    # An older run under the SAME task_id cannot be alive any more: the in-flight lock allows
    # a task_id only once at a time. If "running" still stands there it is a leftover from an
    # aborted worker, and closing it here is more precise than any time limit. The cleanup at
    # worker start only acts after `STALE_GRACE_SEC` and left exactly the cases standing that
    # the restart itself produced (run 714 on 2026-08-07: eight seconds old, thus under the
    # limit, and "running" forever afterwards).
    # BUT only for a top level run: a delegated subrun carries the same task_id as its parent
    # (a joint key for the office) and starts WHILE the parent runs, so that one must under no
    # circumstances be cleared away here.
    if task_id and parent_run_id is None and not spawn_depth:
        alt = (await db.execute(select(Run).where(
            Run.task_id == task_id, Run.status == "running"))).scalars().all()
        for r in alt:
            r.status = "failed"
            r.finished_at = _now()
            r.error = ((r.error or "") + " Abgebrochen: derselbe Auftrag wurde neu "
                       "gestartet (Worker-Neustart).").strip()
    run = Run(issue_id=issue_id, agent=agent, phase=phase, provider=provider, model=model,
              status="running", parent_run_id=parent_run_id, continuation_index=continuation_index,
              task_id=task_id, project_id=project_id, owner_id=owner_id,
              parent_tool_use_id=parent_tool_use_id, spawn_depth=spawn_depth)
    db.add(run)
    await db.commit()
    await db.refresh(run)
    return run


async def _add_step(db: AsyncSession, ctx: office.RunCtx, role: str, tool: str | None,
                    content: str, *, kind: str = "", tool_use_id: str | None = None,
                    target: str | None = None, ok: bool | None = None,
                    duration_ms: int | None = None, in_tokens: int = 0, out_tokens: int = 0,
                    cache_read_tokens: int = 0, provider: str = "", model: str = "") -> None:
    """Write a step row and put it into the live channel right away.

    Writing goes through `office.add_step`, the same way `open_room` takes. There should be no
    second place where a row without the event fields could come into being. Sending happens
    ONLY after the commit: before it the row has no `id`
    and therefore no `seq`. A second sending path would be wrong: `publish_step` swallows
    every error itself, and a Redis outage must not kill an agent run.

    The same holds for the database. A step row is bookkeeping, not a work result, and its
    failure must not cost the work. On 2026-08-07 at 18:00 it did exactly that: a deadlock
    against the schema self healing of the backend made this INSERT fail, the exception broke
    through the loop and ended run 753 after 37 turns. A rollback makes the session usable
    again, and the run keeps writing.
    """
    try:
        step = await office.add_step(
            db, ctx, role=role, kind=kind, content=content, tool=tool, target=target,
            tool_use_id=tool_use_id, ok=ok, duration_ms=duration_ms, in_tokens=in_tokens,
            out_tokens=out_tokens, cache_read_tokens=cache_read_tokens, provider=provider,
            model=model)
    except Exception as exc:  # noqa: BLE001 — bookkeeping is never a reason to give up
        log.warning("Step row not written (%s/%s): %s", role, tool or "—", exc)
        try:
            await db.rollback()
        except Exception:  # noqa: BLE001
            log.exception("The rollback after a failed step row did not succeed")
        return
    # `SessionLocal` runs with expire_on_commit=False, so `step.id` is there without asking.
    await office.publish_step(ctx, step)


async def _end_run(db: AsyncSession, run_id: int, status: str, summary: str = "", error: str = "",
                   iterations: int = 0, wt_fp: str | None = None,
                   in_tok: int = 0, out_tok: int = 0, cache_read: int = 0, *,
                   blocker_kind: str | None = None,
                   ctx: office.RunCtx | None = None) -> None:
    from ..models.agents import CostEntry
    from ..models.ops import ProviderModel
    from ..models.ticket import Issue
    run = await db.get(Run, run_id)
    if not run:
        return
    run.status = status
    run.summary = summary[:4000] if summary else run.summary
    run.error = error[:4000] if error else run.error
    run.iterations = iterations
    run.worktree_fingerprint = wt_fp
    run.input_tokens = in_tok
    run.output_tokens = out_tok
    run.last_text = summary[:2000] if summary else run.last_text
    run.finished_at = _now()
    # What the run hangs on when it ends blocked: "blocked" alone forces every reader to
    # guess the cause from the text.
    if blocker_kind:
        run.blocker_kind = blocker_kind
    # Cost from model prices (when they are in the catalog). cache_read is the input share
    # made cheaper by prompt caching, priced separately with price_cache_read (about 0.1x) so
    # that the saving is visible and the total is right.
    cost = 0.0
    priced: bool | None = None      # None means there was nothing to price (no token)
    if in_tok or out_tok or cache_read:
        pm = (
            await db.execute(
                select(ProviderModel).where(ProviderModel.provider == run.provider,
                                            ProviderModel.model == run.model)
            )
        ).scalar_one_or_none()
        priced = pm is not None
        if pm:
            cost = (in_tok / 1e6 * pm.price_input
                    + out_tok / 1e6 * pm.price_output
                    + cache_read / 1e6 * pm.price_cache_read)
        # The project sits on the run itself since the office exists; the query through the
        # ticket is only the fallback for runs that began before the column.
        project_id = run.project_id
        if project_id is None and run.issue_id:
            project_id = (
                await db.execute(select(Issue.project_id).where(Issue.id == run.issue_id))
            ).scalar_one_or_none()
        # `priced` separates "no catalog entry" from "priced and free": both used to produce
        # the same 0.00, and every gap in the catalog read like a gift.
        db.add(CostEntry(run_id=run.id, issue_id=run.issue_id, agent=run.agent,
                         provider=run.provider, model=run.model, input_tokens=in_tok,
                         output_tokens=out_tok, cache_read_tokens=cache_read,
                         cost_usd=cost, project_id=project_id, priced=priced))
    # Deliberately OUTSIDE the token `if`: a run without tokens used to get not even a
    # written out 0.00 but kept whatever happened to stand there.
    run.cost_usd = cost
    await db.commit()

    if ctx is None:
        return
    # The closing row in the room: without it the agent never walks through the door. The
    # content is the mapping `office._run_end_fields` reads, the same fields the read API
    # pulls from the `runs` row, so the two paths cannot drift apart.
    try:
        await _add_step(db, ctx, "system", None, json.dumps({
            "status": status, "blocker_kind": blocker_kind,
            "summary": (summary or "")[:2000], "error": (error or "")[:2000],
            "iterations": iterations, "in_tokens": in_tok, "out_tokens": out_tok,
            "cache_read_tokens": cache_read, "cost_usd": cost, "cost_priced": priced,
        }, ensure_ascii=False), kind="run_end")
    except Exception:  # noqa: BLE001
        # The room is a spectator, not a participant: a closing row that was not written must
        # not swallow the result of the run (the status is settled above already).
        log.warning("Office: run_end of run %s not written", run_id, exc_info=True)


async def _add_comment(db: AsyncSession, issue_id: int, label: str, body: str) -> None:
    db.add(Comment(issue_id=issue_id, author_id=None, author_label=label, body=body, kind="agent"))
    await db.commit()


# ---------- Hauptschleife ----------

# House rules of the project: convention files as code agents expect them everywhere.
# Deliberately read from the WORKTREE and not copied into the database, because a copy drifts
# away from the repository, and unnoticed at that: the agent would then follow rules a person
# changed three weeks ago. This way the state of the branch being worked on always applies.
CONVENTION_FILES = ("CLAUDE.md", "AGENTS.md", "AGENT.md", "CONVENTIONS.md")
MAX_CONVENTION_CHARS = 12000


def _read_conventions(ws_root: str | None) -> str:
    """The first convention file that exists in the worktree, truncated.

    Only the first: two files next to each other are almost always a copy of one another, and
    two sets of house rules in the same prompt are worse than none. Truncation happens at the
    end and visibly, because a silently halved set of rules would be the worst variant.
    """
    if not ws_root:
        return ""
    for name in CONVENTION_FILES:
        pfad = os.path.join(ws_root, name)
        try:
            if not os.path.isfile(pfad):
                continue
            with open(pfad, encoding="utf-8", errors="replace") as fh:
                text = fh.read().strip()
        except OSError:
            continue
        if not text:
            continue
        if len(text) > MAX_CONVENTION_CHARS:
            text = text[:MAX_CONVENTION_CHARS] + "\n\n… (gekürzt)"
        return (f"# Hausordnung des Projekts ({name})\n\n"
                "Diese Datei liegt im Repo und gilt für diesen Auftrag. Widerspricht sie einer "
                "Anweisung aus den Projekt-Hinweisen weiter unten, gilt die Projekt-Anweisung — "
                "die kennt die Traccoon-Umgebung, die Datei nicht.\n\n" + text)
    return ""


def _build_system_prompt(agent: AgentDef) -> str:
    heute = dt.datetime.now().strftime("%A, %Y-%m-%d %H:%M")
    parts = [agent.system_prompt or f"Du bist {agent.role}.",
             f"Aktuelles Datum/Zeit: {heute}.",
             "Arbeite den Auftrag eigenständig ab. Nutze Tools, wenn nötig. Bist du fertig, antworte mit "
             "einer kurzen Zusammenfassung OHNE Tool-Call. Frage nur bei echten Blockern mit `ask_human`."]
    return "\n\n".join(parts)


async def _owner_gateway(db: AsyncSession, owner_id: int | None) -> tuple[str | None, str | None]:
    """Tool gateway group endpoint plus the owner's token (a hard separation per user).

    No tool configuration on the user means (None, None) and therefore no gateway (registry
    servers only). Does NOT fall back to the global gateway, so a user sees only their servers.
    """
    import os

    from ..core.security import decrypt_secret
    from ..models.user import User

    if not owner_id:
        return None, None
    user = await db.get(User, owner_id)
    if user is None or not user.mcp_group or not user.mcp_token_enc:
        return None, None
    base = os.getenv("MCPJUNGLE_BASE", "http://mcpjungle:8080").rstrip("/")
    return f"{base}/v0/groups/{user.mcp_group}/mcp", decrypt_secret(user.mcp_token_enc)


def _server_spec(r, extra_headers: dict | None = None) -> dict | None:
    """An McpServer row turned into {name, url, headers}. stdio or without a url yields None."""
    from ..core.security import decrypt_secret

    if r.transport not in ("http", "sse") or not r.url:
        log.info("MCP server %s skipped (transport=%s)", r.name, r.transport)
        return None
    headers = dict(r.headers or {})
    if r.env_enc:
        try:
            headers.update(json.loads(decrypt_secret(r.env_enc)))
        except Exception:  # noqa: BLE001
            log.warning("MCP server %s: env could not be decrypted", r.name)
    if extra_headers:
        headers.update(extra_headers)
    return {"name": r.name, "url": r.url, "headers": headers}


async def _agent_mcp(db: AsyncSession, agent: AgentDef, owner_id: int | None = None) -> list[dict]:
    """Registry tool servers for THIS agent:
    - the agent's own instances (server plus filled in variables as headers),
    - plus global or own servers WITHOUT a variable schema (zero configuration), for
      backwards compatibility. Servers WITH variables but without an instance are not loaded."""
    from sqlalchemy import or_, select

    from ..core.security import decrypt_secret
    from ..models.plugins import McpInstance, McpServer

    out: list[dict] = []
    seen: set[Any] = set()  # Server-`id` (hilfsweise `name`) bereits aufgenommener Server → Dedupe

    # 1) Agent-eigene Instanzen
    if agent.id is not None:
        rows = (await db.execute(
            select(McpInstance, McpServer).join(McpServer, McpServer.id == McpInstance.server_id)
            .where(McpInstance.agent_id == agent.id))).all()
        for inst, srv in rows:
            values = {}
            if inst.values_enc:
                try:
                    values = json.loads(decrypt_secret(inst.values_enc))
                except Exception:  # noqa: BLE001
                    log.warning("MCP instance %s: values not decryptable", inst.id)
            spec = _server_spec(srv, extra_headers=values)  # Variablen → Header
            if spec:
                out.append(spec)
                seen.add(srv.id if srv.id is not None else srv.name)

    # 2) Zero configuration servers (no variable schema), global plus own
    # An instance takes precedence (it brings headers and values), so servers already covered
    # here are skipped, otherwise the same server lands twice in the spec list (duplicate
    zc = select(McpServer).where(McpServer.enabled.is_(True))
    zc = zc.where(or_(McpServer.user_id.is_(None), McpServer.user_id == owner_id)
                  if owner_id is not None else McpServer.user_id.is_(None))
    for r in (await db.execute(zc)).scalars().all():
        if r.variables:   # needs configuration, so only through an instance
            continue
        key = r.id if r.id is not None else r.name
        if key in seen:
            continue
        spec = _server_spec(r)
        if spec:
            out.append(spec)
            seen.add(key)
    return out


async def _latest_skill(db: AsyncSession, key: str):
    from sqlalchemy import select

    from ..models.plugins import Skill
    return (await db.execute(select(Skill).where(Skill.key == key, Skill.active.is_(True))
                             .order_by(Skill.version.desc()))).scalars().first()


async def _agent_skills(db: AsyncSession, agent: AgentDef) -> tuple[str, str]:
    """(full text of the autoload skills, menu of the remaining available ones)."""
    autoload = set(agent.autoload_skills or [])
    parts, menu = [], []
    for key in (agent.allowed_skills or []):
        s = await _latest_skill(db, key)
        if not s:
            continue
        if key in autoload:
            head = f"## AKTIVER MODUS: {s.name}" if s.autostart else f"## Skill: {s.name}"
            parts.append(f"{head}\n{s.body}")
        else:
            menu.append(f"- `{s.key}` — {s.name}: {s.description or '(keine Beschreibung)'}")
    menu_text = ""
    if menu:
        menu_text = ("# Verfügbare Skills (bei Bedarf per Tool `load_skill` nachladen)\n"
                     + "\n".join(menu))
    return ("\n\n".join(parts), menu_text)


LOAD_SKILL_TOOL = {
    "type": "function", "function": {
        "name": "load_skill",
        "description": "Lädt den vollständigen Text eines verfügbaren Skills (siehe Liste im Kontext), "
                       "wenn du ihn für die aktuelle Aufgabe brauchst.",
        "parameters": {"type": "object", "properties": {
            "key": {"type": "string", "description": "Der Skill-Key aus der Verfügbar-Liste."}},
            "required": ["key"]},
    }}


# How many turns the look back gets at most: one to remember, one to close.
MAX_REFLEXION_TURNS = 2


async def _reflect(*, db: AsyncSession, mcp, agent: AgentDef, owner_id: int | None,
                   project_key: str, messages: list[dict[str, Any]], summary: str, protokoll,
                   tokens: dict, base_urls: dict) -> tuple[int, int, int]:
    """Look back after a successful run: what lasts goes into the memory (TRA-30).

    An extra model turn over the history of the run, offered ONLY the memory tools, so the
    agent can do nothing here but learn. The normal case is "learned nothing", which costs one
    short turn.

    Returns (input, output, cache_read) to add onto the counters of the run.
    """
    in_tok = out_tok = cache_read = 0
    # The closing answer is not in the history yet (in the branch without tool calls it is not
    # appended), but the look back needs it, because it is the result of the run. The
    # assignment goes in as a `user` turn, NOT as `system`: role=system is rebuilt into a
    # system block for Anthropic (providers/anthropic.py) and would then not stand at the end
    # of the conversation but in the system instruction.
    msgs = list(messages)
    if (summary or "").strip():
        msgs.append({"role": "assistant", "content": summary})
    msgs.append({"role": "user", "content": REFLEXION_PROMPT})
    for _ in range(MAX_REFLEXION_TURNS):
        resp = await router.chat(provider=agent.provider, model=agent.model, messages=msgs,
                                 tools=list(MEMORY_TOOLS), temperature=agent.temperature,
                                 max_tokens=1024, fallback=agent.fallback,
                                 fallback_model=agent.fallback_model, tokens=tokens,
                                 # 1024 tokens are too few for thinking plus a note: at the
                                 # default level the thinking alone eats the budget and the
                                 # look back comes back empty.
                                 base_urls=base_urls, effort="low")
        in_tok += int(resp.usage.get("input_tokens", 0) or 0)
        out_tok += int(resp.usage.get("output_tokens", 0) or 0)
        cache_read += int(resp.cache_read_tokens or 0)
        if not resp.tool_calls:
            break
        msgs.append((resp.raw.get("choices") or [{}])[0].get("message") or {
            "role": "assistant", "content": resp.text, "tool_calls": []})
        for call in resp.tool_calls:
            if call.name in MEMORY_TOOL_NAMES:
                out = await call_memory_tool(db, mcp, owner_id, call.name, call.arguments,
                                             agent.role, project_key)
            else:
                out = f"FEHLER: In der Rückschau ist nur '{', '.join(sorted(MEMORY_TOOL_NAMES))}' erlaubt."
            # Deliberately stays one summarised row without a `kind`: the look back is the
            # wrap up of the run, not work on the assignment, and it should not fill the room
            # with tools. The old data path still splits it cleanly while reading.
            await protokoll("tool", call.name,
                      f"Rückschau: args={json.dumps(call.arguments, ensure_ascii=False)[:400]}\n→ {out[:500]}")
            msgs.append({"role": "tool", "tool_call_id": call.id, "name": call.name,
                         "content": out[:2000]})
    return in_tok, out_tok, cache_read


async def run_agent(*, db: AsyncSession, agent: AgentDef, issue: dict, project: dict,
                    mode: str = "execute", permissions: list[dict] | None = None,
                    ws_root: str | None = None, gate_on: bool = False,
                    tokens: dict[str, str | None] | None = None,
                    base_urls: dict[str, str | None] | None = None, verify_command: str = "",
                    strict_success: bool = False, owner_id: int | None = None,
                    screenshot_enabled: bool = False, testenv_url: str = "",
                    continuation_index: int = 0, continuation_hint: str = "",
                    comment_history: list[dict] | None = None, history_title: str = "",
                    parent_run_id: int | None = None, parent_tool_use_id: str | None = None,
                    task_id: str = "",
                    depth: int = 0, delegate_loader=None,
                    assistant_task_id: int | None = None) -> RunResult:
    permissions = permissions or []
    tokens = tokens or {}
    base_urls = base_urls or {}
    issue_id = issue["id"]

    # Attachments of the ticket (metadata), for the context hint and the read_attachment tool.
    from ..models.ticket import Attachment
    _att_rows = (await db.execute(
        select(Attachment.filename, Attachment.mime_type, Attachment.size)
        .where(Attachment.issue_id == issue_id).order_by(Attachment.id))).all()

    # `project["id"]` is None on job and assistant runs, which is exactly why the column is
    # nullable: such runs belong to no project, only to their person.
    run = await _start_run(db, issue_id, agent.name, mode, agent.provider,
                           agent.model or agent.provider, parent_run_id, continuation_index,
                           task_id=task_id, project_id=project.get("id"), owner_id=owner_id,
                           parent_tool_use_id=parent_tool_use_id, spawn_depth=depth)
    run_id = run.id
    # The context carries the seq counter of the run: `_end_run` writes the closing row after
    # the loop (and with it `protokoll`) has long been left, and a counter in the closure
    # could not be counted on there.
    ctx = office.RunCtx.from_run(run, issue_key=str(issue.get("key") or ""))

    async def protokoll(role: str, tool: str | None, content: str, *, kind: str = "",
                        **felder: Any) -> None:
        await _add_step(db, ctx, role, tool, content, kind=kind, **felder)

    # The agent enters the room, and it says why: `run_start` plus the assignment as
    # `user_message`, beides in einer Transaktion.
    await office.open_room(db, ctx, agent=agent, mode=mode, issue=issue)

    messages: list[dict[str, Any]] = [
        {"role": "system", "content": _build_system_prompt(agent)},
        {"role": "user", "content": f"# Auftrag: {issue['summary']}\n\n{issue.get('description') or ''}".strip()},
    ]
    # Skills: autoload as full text, available (non auto) ones as a menu plus the load_skill tool.
    autoload_text, skill_menu = await _agent_skills(db, agent)
    if autoload_text:
        messages.append({"role": "system", "content": autoload_text})
    if skill_menu:
        messages.append({"role": "system", "content": skill_menu})
    # First the house rules from the repository, then the project hints: the database has the
    # last word, because that is where what applies ONLY here stands (worktree instead of the
    # live folder, `check` instead of host commands, no deploy by hand).
    konventionen = _read_conventions(ws_root)
    if konventionen:
        messages.append({"role": "system", "content": konventionen})
    if project.get("system_prompt"):
        messages.append({"role": "system", "content": project["system_prompt"]})
    if mode == "plan" and issue.get("plan"):
        messages.append({"role": "user", "content":
                         "# Bestehender Plan (überarbeite ihn anhand der Kommentare)\n\n" + issue["plan"]})
    elif (plan_text := (issue.get("plan") or "").strip()):
        # THE PLAN BELONGS IN THE EXECUTION. It used to be passed to `run_agent` but only used
        # in planning mode, so the developer worked from the ticket description. On TRA-31
        # that description was a report of symptoms ("find the cause, evaluate job_runs"),
        # while the approved plan had named the cause long before, with file and line number.
        # The result on 2026-08-07: three runs, 155 turns, no
        # agent worked out the finished analysis for itself all over again.
        messages.append({"role": "user", "content":
            "# Freigegebener Umsetzungsplan — das ist dein Auftrag\n\n" + plan_text +
            "\n\nDieser Plan ist geprüft und freigegeben: seine Fundstellen sind belegt, "
            "die Analyse ist getan. Arbeite ihn ab, statt sie zu wiederholen. Weiche nur ab, "
            "wo der Code dem Plan widerspricht — und schreibe dann ins Ergebnis, was du "
            "anders gemacht hast und warum."})
    if continuation_index > 0 and continuation_hint:
        messages.append({"role": "system", "content":
            f"## Fortsetzung (Runde {continuation_index})\nWorktree-Stand ist erhalten. Letzter Stand:\n"
            f"{continuation_hint}\nArbeite direkt weiter, prüfe den Build-Status, schließe offene Schritte ab."})
    elif mode != "plan" and issue.get("id"):
        # No orderly end, no handover, and the successor starts from zero although the
        # worktree already carries half the work. For an abort (worker restart, crash) the
        # handover can be built from the data: which files the predecessor touched stands in
        # its step rows. Costs no model turn.
        if (abbruch := await _abbruch_uebergabe(db, int(issue["id"]), run_id)):
            messages.append({"role": "system", "content": abbruch})
    if comment_history:
        thread = "\n".join(f"- **{c['label']}** ({c['role']}): {c['body']}" for c in comment_history)
        messages.append({"role": "user", "content":
                         (history_title or "# Kommentar-Verlauf (Rückfragen & Antworten)") +
                         "\n" + thread +
                         "\n\nBerücksichtige besonders die Antworten des Nutzers (role=user)."})
    if _att_rows:
        _lst = "\n".join(f"- {fn} ({mt or 'unbekannt'}, {sz} Bytes)" for fn, mt, sz in _att_rows)
        messages.append({"role": "system", "content":
                         "# Anhänge am Ticket\nDieses Ticket hat Datei-Anhänge. Nutze das Tool "
                         "`read_attachment` mit dem Dateinamen, um einen Anhang anzusehen — "
                         "Bilder/Screenshots werden dir als Bild gezeigt.\n" + _lst})

    gw_url, gw_token = await _owner_gateway(db, owner_id)
    # The token counters live OUTSIDE the `try`: the outer `except` below passes them to
    # `_end_run`, and if they were bound only inside the `async with`, the rescue path of all
    # things would raise a NameError instead of saving the tokens.
    in_tok = out_tok = cache_read = 0
    # Empty string (not None) means NO gateway; no fallback to a global one (hard separation).
    try:
        async with mcp_session(agent.name, servers=await _agent_mcp(db, agent, owner_id),
                               gateway_url=gw_url or "", gateway_token=gw_token or "") as mcp:
            mcp_tools = await mcp.list_tools()
            openai_tools = [t.to_openai() for t in mcp_tools if agent.tool_allowed(t.name)]
            openai_tools.append(ASK_HUMAN_TOOL)
            if _att_rows:  # the ticket has attachments, so offer the read tool (read only, always allowed)
                openai_tools.append(READ_ATTACHMENT_TOOL)
            if skill_menu:  # there are available non auto skills, so offer the loading tool
                openai_tools.append(LOAD_SKILL_TOOL)
            if mode != "plan":
                openai_tools.append(CONTINUE_LATER_TOOL)
                openai_tools.append(OPEN_TASKS_TOOL)
                if agent.can_delegate and delegate_loader is not None and depth < MAX_DELEGATION_DEPTH:
                    openai_tools.append(_delegate_tool(agent.delegate_to))
            # Native capability tools: the coarse ability gate (can_code/can_read_code/
            # screenshot) AND additionally deny by default through the allowlist (tool_allowed).
            _maybe = lambda t: openai_tools.append(t) if agent.tool_allowed(t["function"]["name"]) else None
            if ws_root and (agent.can_code or agent.can_read_code):
                _maybe(FS_READ_TOOL); _maybe(FS_LIST_TOOL)
                if await _codegraph.available():
                    _maybe(CODEGRAPH_TOOL)
                if screenshot_enabled:
                    _maybe(SCREENSHOT_TOOL)
                if mode != "plan" and agent.can_code:
                    _maybe(FS_WRITE_TOOL); _maybe(FS_EDIT_TOOL); _maybe(CHECK_TOOL)
                    # Offer no tool whose answer is settled already: without a stack directory
                    # of its own (or with Traccoon itself as the target) the deployer rejects
                    # every request. A tool that can only fail is an invitation to burn a turn
                    # on it.
                    if not deploy_gesperrt(project.get("stack_dir", "")):
                        _maybe(DEPLOY_TOOL)
                    messages.append({"role": "system", "content": CODE_WORKFLOW})
            # Traccoon control tools: only with a user context (permission check) and
            # `traccoon_*` in the allowlist. They need no worktree.
            if owner_id:
                for _t in TRACCOON_TOOLS:
                    _maybe(_t)
            if mode == "plan":
                openai_tools.append(SUBMIT_PLAN_TOOL)

            # Memory (TRA-30): learned rules from the owner's vault. Reading happens ON THE
            # SERVER, so the `oneOf` addressing of the vault tool server does not depend on the
            # model (reasoning in tools_memory). Without a vault folder nothing happens.
            mem_root = await memory_root(db, owner_id) if agent.learns else ""
            if mem_root:
                for _t in MEMORY_TOOLS:
                    openai_tools.append(_t)
                try:
                    _mem = await read_memory(mcp, mem_root, agent.role, project.get("key") or "")
                except Exception:  # noqa: BLE001
                    _mem = ""      # no vault reachable: run without memory, do not abort
                if _mem:
                    messages.append({"role": "system", "content":
                        "# Gedächtnis (früher gelernt, gilt weiter)\n" + _mem +
                        "\n\nDas hat dir dein Mensch beigebracht — halte dich daran, ohne dass er "
                        "es wiederholen muss. Widerspricht der aktuelle Auftrag einer Erinnerung, "
                        "gilt der Auftrag: korrigiere die Erinnerung dann mit `vergiss` und "
                        "`erinnere_dich`."})

            # Vault-Projektkontext (MOC + Dateibaum) laden, falls konfiguriert (via obsidian-MCP)
            moc_path = project.get("vault_moc_path")
            if moc_path:
                try:
                    name = moc_path.rstrip("/").split("/")[-1]
                    moc = await mcp.call("obsidian__obsidian_get_note",
                                         {"format": "content", "target": {"type": "path",
                                          "path": f"{moc_path}/{name}.md"}})
                    tree = await mcp.call("obsidian__obsidian_list_notes", {"path": moc_path, "depth": 2})
                    messages.append({"role": "user", "content":
                        f"# Projektkontext (Vault: {moc_path})\n{(moc or '')[:6000]}\n\n## Dateien\n{(tree or '')[:2000]}"})
                except Exception:  # noqa: BLE001
                    pass  # tool servers not configured, so no vault context

            last_text = ""
            empties = 0
            build_gate_fails = 0
            letzter_kontext = 0     # real context size of the last call (for the compaction)
            frist = (asyncio.get_running_loop().time() + MAX_RUN_SECONDS) if MAX_RUN_SECONDS else 0.0
            # Has this run delivered anything yet (a change or a plan)? That decides whether a
            # reminder follows, not how much it has read.
            ergebnis_tools = ERGEBNIS_TOOLS["plan" if mode == "plan" else "execute"] & {
                t["function"]["name"] for t in openai_tools}
            ergebnis_da = False
            ermahnt = 0
            grenze_grund = "Iterations-Limit erreicht."
            iteration = 0       # in case max_iterations is 0, the loop never runs
            for iteration in range(1, agent.max_iterations + 1):
                if frist and asyncio.get_running_loop().time() > frist:
                    # As with the token budget: `break` falls into the loop_exhausted ending.
                    gelaufen = int(MAX_RUN_SECONDS + asyncio.get_running_loop().time() - frist)
                    grenze_grund = f"Zeitlimit erreicht ({gelaufen}s, Grenze {int(MAX_RUN_SECONDS)}s)."
                    log.warning("Run %s: Zeitlimit erreicht (%ds) → loop_exhausted", run_id, gelaufen)
                    await protokoll("system", None,
                              f"⚠️ {grenze_grund} → loop_exhausted (Fortsetzung in frischem Run)",
                              kind="system")
                    break
                if iteration == max(2, agent.max_iterations - 2):
                    messages.append({"role": "system", "content":
                        "⚠️ Du näherst dich dem Iterations-Limit. Wenn du NICHT unmittelbar vor dem Abschluss "
                        "stehst: `continue_later` mit Zusammenfassung. Nur bei echter Blockade: `ask_human`."})
                # Remind while it still helps: budget spent without any result at all. On
                # 2026-08-07 UNI-12 read 190 files across three runs and wrote not a line. The
                # only reminder came at round 78 of 80, long after the time was gone.
                if ergebnis_tools and not ergebnis_da:
                    verbraucht = max(
                        iteration / max(1, agent.max_iterations),
                        ((MAX_RUN_SECONDS - (frist - asyncio.get_running_loop().time()))
                         / MAX_RUN_SECONDS) if frist else 0.0)
                    faellig = ermahnungen_faellig(verbraucht, ermahnt)
                    while ermahnt < faellig:
                        ermahnt += 1
                        text = ermahnung_text(mode, verbraucht, scharf=ermahnt >= len(ERMAHNUNG_BEI))
                        messages.append({"role": "system", "content": text})
                        await protokoll("system", None,
                                        f"⚠️ {int(verbraucht * 100)} % Budget ohne Ergebnis — nachgehakt",
                                        kind="system")
                # Shorten the context BEFORE it bursts the provider. What is measured is the
                # real context size of the last call; without `max_context_tokens` nothing happens.
                if agent.max_context_tokens and letzter_kontext:
                    _neu = await _kompaktiere(
                        db, messages=messages, grenze_tokens=agent.max_context_tokens,
                        gemessen=letzter_kontext, owner_id=owner_id, agent=agent,
                        tokens=tokens, base_urls=base_urls)
                    if _neu is not None:
                        await protokoll("system", None,
                                  f"Verlauf kompaktiert: {len(messages)} → {len(_neu)} Nachrichten "
                                  f"(Kontext {letzter_kontext} von {agent.max_context_tokens}).",
                                  kind="system")
                        messages = _neu
                        letzter_kontext = 0     # measurement spent: measure again, then shorten again
                try:
                    resp = await router.chat(provider=agent.provider, model=agent.model, messages=messages,
                                             tools=openai_tools, temperature=agent.temperature,
                                             max_tokens=agent.max_tokens, fallback=agent.fallback,
                                             fallback_model=agent.fallback_model,
                                             web_search=agent.web_search, tokens=tokens,
                                             base_urls=base_urls, effort=agent.effort)
                except ProviderError as exc:
                    await protokoll("system", None, f"Provider-Fehler: {exc}", kind="system")
                    # The turns so far are paid for even when the last one failed. Without the
                    # tokens here a provider error lost the whole run from the cost calculation.
                    await _end_run(db, run_id, "failed", error=str(exc), iterations=iteration,
                                   in_tok=in_tok, out_tok=out_tok, cache_read=cache_read, ctx=ctx)
                    return RunResult("failed", str(exc), iteration, run_id=run_id)

                in_tok += int(resp.usage.get("input_tokens", 0) or 0)
                out_tok += int(resp.usage.get("output_tokens", 0) or 0)
                # Accumulate cached input tokens separately (NOT into in_tok, because
                # usage.input_tokens is already the uncached remainder; the runaway cap in
                # dispatcher._process keeps evaluating runs.input_tokens).
                cache_read += int(resp.cache_read_tokens or 0)
                # For the compaction the ENTIRE context of this call counts, so the uncached
                # remainder PLUS the cached share. Taking only `input_tokens` would be almost
                # zero on a good cache hit, and the limit would never take effect.
                letzter_kontext = (int(resp.usage.get("input_tokens", 0) or 0)
                                   + int(resp.cache_read_tokens or 0))
                if in_tok >= MAX_RUN_INPUT_TOKENS:
                    # Hard token budget reached: end the run exactly as on the iteration limit.
                    # `break` falls into the loop_exhausted ending below (the same
                    # _end_run/RunResult path) so that the continuation semantics apply.
                    grenze_grund = f"Token-Budget erreicht ({in_tok} ≥ {MAX_RUN_INPUT_TOKENS})."
                    log.warning("Run %s: Token-Budget erreicht (%d ≥ %d) → loop_exhausted",
                                run_id, in_tok, MAX_RUN_INPUT_TOKENS)
                    await protokoll("system", None,
                              f"⚠️ Token-Budget erreicht ({in_tok} ≥ {MAX_RUN_INPUT_TOKENS}) "
                              f"→ loop_exhausted (Fortsetzung in frischem Run)", kind="system")
                    break
                # The content stays verbatim as before (`AgentMonitor` reads it that way), but
                # the row now carries the tokens of THIS turn: only then does the cost curve
                # grow by the second instead of appearing in one jump at the end of the run.
                # `kind` separates real text from a pure tool turn, which costs as well but
                # said nothing and should say nothing in the room.
                # Provider and model come from the ANSWER: on a fallback that is not the one
                # configured on the agent, and with the wrong one the turn would be mispriced.
                await protokoll("assistant", None, resp.text or "(Tool-Call)",
                                kind="agent_text" if (resp.text or "").strip() else "usage",
                                in_tokens=int(resp.usage.get("input_tokens", 0) or 0),
                                out_tokens=int(resp.usage.get("output_tokens", 0) or 0),
                                cache_read_tokens=int(resp.cache_read_tokens or 0),
                                provider=resp.provider or agent.provider,
                                model=resp.model or agent.model)
                if resp.text:
                    last_text = resp.text

                if not resp.tool_calls:
                    if (resp.text or "").strip():
                        if (agent.can_code and mode != "plan" and ws_root
                                and strict_success and not verify_command):
                            # Strict acceptance without a verify command: success would not be provable.
                            err = ("Strenge Abnahme ist aktiv, aber das Projekt hat keinen verify_command. "
                                   "Ohne grünen Prüflauf gilt der Lauf nicht als erfolgreich.")
                            await _end_run(db, run_id, "failed", error=err, iterations=iteration,
                                           in_tok=in_tok, out_tok=out_tok, cache_read=cache_read,
                                           ctx=ctx)
                            return RunResult("failed", err, iteration, run_id=run_id)
                        if agent.can_code and mode != "plan" and ws_root and verify_command:
                            verdict = await _do_check(ws_root, verify_command)
                            if verdict.startswith("❌"):
                                build_gate_fails += 1
                                if build_gate_fails > MAX_BUILD_GATE:
                                    await _end_run(db, run_id, "loop_exhausted", error=verdict,
                                                   iterations=iteration, in_tok=in_tok, out_tok=out_tok,
                                                   cache_read=cache_read, ctx=ctx)
                                    return RunResult("loop_exhausted", verdict, iteration, run_id=run_id)
                                messages.append({"role": "system", "content":
                                    "⛔ ABSCHLUSS BLOCKIERT: Build ist ROT. Behebe die Ursache (nichts weglöschen) "
                                    "und arbeite weiter:\n\n" + verdict})
                                continue
                        # Look back: what was a lasting rule? (TRA-30) Only on success, because
                        # an aborted run holds no solid lesson. Errors stay here: the run was
                        # successful, and the look back does not change that.
                        if mem_root:
                            try:
                                _ri, _ro, _rc = await _reflect(
                                    db=db, mcp=mcp, agent=agent, owner_id=owner_id,
                                    project_key=project.get("key") or "", messages=messages,
                                    summary=resp.text, protokoll=protokoll, tokens=tokens,
                                    base_urls=base_urls)
                                in_tok += _ri; out_tok += _ro; cache_read += _rc
                            except Exception as exc:  # noqa: BLE001
                                await protokoll("system", None, f"Rückschau übersprungen: {exc}",
                                                kind="system")
                        await _end_run(db, run_id, "success", summary=resp.text, iterations=iteration,
                                       in_tok=in_tok, out_tok=out_tok, cache_read=cache_read, ctx=ctx)
                        return RunResult("done", resp.text, iteration, summary=resp.text, run_id=run_id)
                    empties += 1
                    if empties >= 2:
                        # The failure cost something as well: the turns before it are paid for.
                        await _end_run(db, run_id, "failed", error="Leere Modell-Antwort.",
                                       iterations=iteration, in_tok=in_tok, out_tok=out_tok,
                                       cache_read=cache_read, ctx=ctx)
                        return RunResult("failed", "Leere Modell-Antwort.", iteration, run_id=run_id)
                    messages.append({"role": "system", "content":
                        "Deine letzte Antwort war leer. Rufe ein Tool auf oder liefere eine Abschluss-Zusammenfassung."})
                    continue

                assistant_msg = (resp.raw.get("choices") or [{}])[0].get("message") or {
                    "role": "assistant", "content": resp.text, "tool_calls": []}
                messages.append(assistant_msg)

                for call in resp.tool_calls:
                    if call.name == "ask_human":
                        question = (call.arguments.get("question") or "").strip()
                        if not question:
                            messages.append({"role": "tool", "tool_call_id": call.id, "name": call.name,
                                             "content": "Keine Rückfrage nötig – antworte direkt."})
                            continue
                        # Blocker and comment hang on the ticket, and a projectless run
                        # (assistant, job) has none. Without this brake the insert fails on NOT
                        # NULL (blockers.issue_id), the session dies on a PendingRollbackError
                        # and the question NEVER reaches the person (the task stays 'running').
                        if issue_id:
                            db.add(Blocker(issue_id=issue_id, run_id=run_id, question=question))
                            await db.commit()
                            await _add_comment(db, issue_id, agent.name, question)
                        await _end_run(db, run_id, "blocked", summary=question, iterations=iteration,
                                       in_tok=in_tok, out_tok=out_tok, cache_read=cache_read,
                                       blocker_kind="ask_human", ctx=ctx)
                        return RunResult("blocked", question, iteration, run_id=run_id, blocker_kind="ask_human")

                    if call.name == "continue_later":
                        s = (call.arguments.get("summary") or "").strip()
                        fp = await _gitops.worktree_fingerprint(ws_root) if ws_root else None
                        await _end_run(db, run_id, "loop_exhausted", summary=s, iterations=iteration,
                                       wt_fp=fp, in_tok=in_tok, out_tok=out_tok, cache_read=cache_read,
                                       ctx=ctx)
                        return RunResult("loop_exhausted", s, iteration, run_id=run_id)

                    if call.name == "submit_plan" and mode == "plan":
                        plan = (call.arguments.get("plan") or "").strip()
                        if not plan:
                            messages.append({"role": "tool", "tool_call_id": call.id, "name": call.name,
                                             "content": "Plan war leer – vollständigen Plan einreichen."})
                            continue
                        psum = (call.arguments.get("summary") or "").strip()
                        await _end_run(db, run_id, "planned", summary="Plan erstellt", iterations=iteration,
                                       in_tok=in_tok, out_tok=out_tok, cache_read=cache_read, ctx=ctx)
                        return RunResult("planned", plan, iteration, summary=psum, run_id=run_id)

                    # Permission gate for mutating external tools
                    if gate_on and call.name not in ("open_tasks",) and perms.is_gated(call.name):
                        resource = perms.resource_of(call.name, call.arguments)
                        if not await perms.take_grant(db, issue_id, call.name, resource):
                            action = perms.evaluate(permissions, call.name, resource)
                            if action == "deny":
                                messages.append({"role": "tool", "tool_call_id": call.id, "name": call.name,
                                                 "content": f"FEHLER: Berechtigung verweigert (deny) für "
                                                 f"`{call.name}` auf `{resource or '—'}`."})
                                continue
                            if action == "ask":
                                await perms.create_perm_request(db, issue_id, run_id, call.name, resource)
                                await _add_comment(db, issue_id, agent.name,
                                                   f"⚙️ Berechtigung nötig: `{call.name}` auf `{resource or '—'}`")
                                await _end_run(db, run_id, "blocked", summary=f"Berechtigung: {call.name}",
                                               iterations=iteration, in_tok=in_tok, out_tok=out_tok,
                                               cache_read=cache_read, blocker_kind="permission", ctx=ctx)
                                return RunResult("blocked", f"Berechtigung: {call.name}", iteration,
                                                 run_id=run_id, blocker_kind="permission")

                    # Assistent-Tool-Gate: externe mutierende MCP-Aktionen brauchen Freigabe.
                    # traccoon_* control tools are exempt (already bounded by the owner's
                    # rights), EXCEPT the destination call: it acts on a foreign system, and
                    # whether it changes anything is only visible in the method (GET reads,
                    # POST/PUT/DELETE write). And except the job writing tools: a schedule keeps
                    # acting long after the run that created it is over.
                    _gated = (perms.is_gated(call.name) and call.name not in TRACCOON_TOOL_NAMES) \
                        or call.name in TRACCOON_GATED_TOOLS
                    if call.name == "traccoon_http_call":
                        _gated = str(call.arguments.get("method") or "GET").upper() not in (
                            "GET", "HEAD", "OPTIONS")
                    if assistant_task_id and _gated:
                        _atask = await db.get(AssistantTask, assistant_task_id)
                        if _atask is not None:
                            _res = perms.resource_of(call.name, call.arguments)
                            _dec = await gate_check(db, _atask, owner_id, call.name, _res)
                            if _dec == "deny":
                                messages.append({"role": "tool", "tool_call_id": call.id, "name": call.name,
                                                 "content": f"FEHLER: Freigabe verweigert (nie) für `{call.name}`."})
                                continue
                            if _dec == "ask":
                                await _end_run(db, run_id, "blocked", summary=f"Freigabe nötig: {call.name}",
                                               iterations=iteration, in_tok=in_tok, out_tok=out_tok,
                                               cache_read=cache_read, blocker_kind="assistant_perm",
                                               ctx=ctx)
                                return RunResult("blocked", f"Freigabe nötig: {call.name}", iteration,
                                                 run_id=run_id, blocker_kind="assistant_perm")

                    # The tool start deliberately stands HERE, after every gate and immediately
                    # before execution. Every gate above does `continue` or `return`; a start
                    # before them would leave a tool that is never closed, and an agent would
                    # sit in the room typing forever.
                    _ziel = office.tool_target(call.name, call.arguments)
                    _args_json = json.dumps(call.arguments, ensure_ascii=False)
                    # A monotonic clock, the same one as the runtime limit above: the wall
                    # clock may jump, a measured duration may not.
                    _t0 = asyncio.get_running_loop().time()
                    await protokoll("tool", call.name, _args_json[:400], kind="tool_start",
                                    tool_use_id=call.id, target=_ziel)

                    if call.name == "open_tasks":
                        result: Any = await _open_tasks(db)
                    elif call.name == "load_skill":
                        skey = (call.arguments.get("key") or "").strip()
                        if skey not in (agent.allowed_skills or []):
                            result = f"FEHLER: Skill '{skey}' ist diesem Agenten nicht zugewiesen."
                        else:
                            sk = await _latest_skill(db, skey)
                            result = (f"## Skill: {sk.name}\n{sk.body}" if sk
                                      else f"FEHLER: Skill '{skey}' nicht gefunden.")
                    elif call.name == "delegate" and agent.can_delegate and delegate_loader is not None:
                        sub_role = (call.arguments.get("role") or "").strip()
                        sub_task = (call.arguments.get("task") or "").strip()
                        sub_agent = await delegate_loader(sub_role)
                        if sub_agent is None:
                            result = f"FEHLER: Rolle '{sub_role}' nicht verfügbar."
                        else:
                            sub = await run_agent(
                                db=db, agent=sub_agent,
                                issue={"id": issue_id, "key": issue["key"], "summary": sub_task,
                                       "description": sub_task, "plan": None},
                                project=project, mode="execute", permissions=permissions, ws_root=ws_root,
                                gate_on=gate_on, tokens=tokens, base_urls=base_urls, verify_command=verify_command,
                                strict_success=strict_success, owner_id=owner_id,
                                screenshot_enabled=screenshot_enabled, testenv_url=testenv_url,
                                depth=depth + 1, delegate_loader=delegate_loader, parent_run_id=run_id,
                                # The joint key: `delegate` awaits the subrun inline, so the
                                # tool row only appears at its END. The moment of the spawn
                                # therefore comes from the `run_start` of the child, and that
                                # one needs the tool id of the parent.
                                parent_tool_use_id=call.id,
                                task_id=task_id)
                            if sub.status == "blocked":
                                # Subagent blocked: pass the question on to the person
                                await _end_run(db, run_id, "blocked", summary=sub.text, iterations=iteration,
                                               in_tok=in_tok, out_tok=out_tok, cache_read=cache_read,
                                               blocker_kind=sub.blocker_kind or "question", ctx=ctx)
                                return RunResult("blocked", sub.text, iteration, run_id=run_id,
                                                 blocker_kind=sub.blocker_kind or "question")
                            result = f"[Sub-Agent {sub_role} → {sub.status}]\n{sub.text[:2000]}"
                    elif call.name in FS_TOOL_NAMES:
                        result = _fs_dispatch(call.name, ws_root, call.arguments)
                        # Delivered means what actually worked: a rejected write attempt must
                        # not switch the reminders off.
                        if call.name in ergebnis_tools and not result.startswith("FEHLER"):
                            ergebnis_da = True
                    elif call.name == "codegraph":
                        result = await _codegraph.query(
                            ws_root, (call.arguments.get("command") or "explore").strip(),
                            (call.arguments.get("query") or "").strip())
                    elif call.name == "check":
                        result = await _do_check(ws_root, verify_command)
                    elif call.name == "deploy":
                        result = await _do_deploy(db, issue_id, project["id"],
                                                  project.get("stack_dir", ""), ws_root)
                    elif call.name == "screenshot":
                        result = await _do_screenshot(call.arguments, testenv_url or project.get("live_url", ""))
                    elif call.name == "read_attachment":
                        result = await _do_read_attachment(db, issue_id, call.arguments)
                    elif call.name in TRACCOON_TOOL_NAMES:
                        result = await call_traccoon_tool(db, owner_id, call.name, call.arguments,
                                                          assistant_task_id)
                    elif call.name in MEMORY_TOOL_NAMES:
                        result = await call_memory_tool(db, mcp, owner_id, call.name, call.arguments,
                                                        agent.role, project.get("key") or "")
                    elif not agent.tool_allowed(call.name):
                        result = f"FEHLER: Tool '{call.name}' ist für diesen Agenten nicht erlaubt."
                    else:
                        try:
                            result = await mcp.call(call.name, call.arguments)
                        except Exception as exc:  # noqa: BLE001
                            result = f"TOOL-FEHLER: {exc}"

                    # The counterpart to the start above: only this closes the tool again.
                    _dauer_ms = max(0, int((asyncio.get_running_loop().time() - _t0) * 1000))
                    if isinstance(result, list):
                        # An image or block result: the call came back, an error would have
                        # become a string, so success is proven here.
                        await protokoll("tool", call.name, "(Bild/Block-Ergebnis)",
                                        kind="tool_result", tool_use_id=call.id, target=_ziel,
                                        ok=True, duration_ms=_dauer_ms)
                        messages.append({"role": "tool", "tool_call_id": call.id, "name": call.name,
                                         "content": result})
                    else:
                        # `traccoon_http_call` already got its limit from the destination
                        # (Destination.max_response_chars, TRA-31) and brings it along in the
                        # answer. The blanket cap would take it back here, hence the wider
                        # frame for this tool.
                        cap = MAX_HTTP_TOOL_CHARS if call.name == "traccoon_http_call" else 8000
                        # `tool_ok` knows only the PROVEN error (the prefix) and otherwise
                        # "unknown". The runtime knows more here: the call came back, and every
                        # exception would have become "TOOL-FEHLER:" above. So "no error
                        # prefix" counts as success, and only a proven True lets `step_events`
                        # derive a `file_edit` from it at all.
                        _ok = office.tool_ok(result)
                        await protokoll("tool", call.name, result[:2000], kind="tool_result",
                                        tool_use_id=call.id, target=_ziel,
                                        ok=True if _ok is None else _ok, duration_ms=_dauer_ms)
                        messages.append({"role": "tool", "tool_call_id": call.id, "name": call.name,
                                         "content": result[:cap]})

            # `grenze_grund` names WHICH limit ended the run: this used to say "iteration
            # limit" even after the token budget, which twists the search for a cause. The
            # rest is the handover to the continuation: findings, what is done, the next step,
            # taken from the HISTORY and not from the last sentence. Without it every
            # continuation run started from zero (UNI-12: three runs, not a line of code).
            exhausted = await _uebergabe(
                db, messages=messages, grund=grenze_grund, letzter_text=last_text,
                owner_id=owner_id, agent=agent, tokens=tokens, base_urls=base_urls)
            fp = await _gitops.worktree_fingerprint(ws_root) if ws_root else None
            # Report the ACTUAL number of rounds: the time and token limits end a run early,
            # and "40 rounds" after two rounds sends the search for a cause astray.
            await _end_run(db, run_id, "loop_exhausted", summary=exhausted, iterations=iteration,
                           wt_fp=fp, in_tok=in_tok, out_tok=out_tok, cache_read=cache_read, ctx=ctx)
            return RunResult("loop_exhausted", exhausted, iteration, run_id=run_id)

    except Exception as exc:  # noqa: BLE001
        log.exception("run_agent(%s) Laufzeitfehler", agent.name)
        # The tokens here as well: the run died somewhere in the middle, but it is paid for
        # all the same. That is why the counters stand above, BEFORE the `try`.
        await _end_run(db, run_id, "failed", error=str(exc), in_tok=in_tok, out_tok=out_tok,
                       cache_read=cache_read, ctx=ctx)
        return RunResult("failed", str(exc), 0, run_id=run_id)


ABBRUCH_FENSTER_MIN = 120


async def _abbruch_uebergabe(db: AsyncSession, issue_id: int, run_id: int) -> str:
    """What the aborted predecessor already did, from the step rows and without a model turn.

    A run that ends in order hands over (`compaction.uebergabe`). An aborted one does not: at
    the worker restart on 2026-08-07 runs 753 and 754 lost their history, the successors began
    from zero and read the same files again, although their changes had long been in the
    worktree. The facts for it are in the database, and reading them costs one query instead
    of a summary.

    Deliberately only facts (files, number of rounds, last sentence) and no interpreted state:
    what the predecessor intended nobody knows any more, what it touched is settled.
    """
    import datetime as _dt

    from ..models.agents import Run, RunStep
    grenze = _dt.datetime.now(_dt.UTC) - _dt.timedelta(minutes=ABBRUCH_FENSTER_MIN)
    vor = (await db.execute(
        select(Run).where(Run.issue_id == issue_id, Run.id != run_id, Run.status == "failed",
                          Run.finished_at.isnot(None), Run.finished_at > grenze)
        .order_by(Run.id.desc()).limit(1))).scalars().first()
    if vor is None:
        return ""
    schritte = (await db.execute(
        select(RunStep.tool_name, RunStep.target).where(
            RunStep.run_id == vor.id, RunStep.kind == "tool_result",
            RunStep.tool_name.in_(("fs_write", "fs_edit")), RunStep.ok.is_(True)))).all()
    dateien = sorted({t for _, t in schritte if t})
    if not dateien:
        return ""      # nothing written, nothing to hand over, the successor searches itself
    zuege = (await db.scalar(select(func.count()).select_from(RunStep).where(
        RunStep.run_id == vor.id, RunStep.role == "assistant"))) or 0
    letzter = (vor.last_text or "").strip()[:600]
    return ("## Vorlauf abgebrochen — der Worktree trägt seine Arbeit bereits\n"
            f"Der vorige Lauf (#{vor.id}) endete nach {zuege} Zügen unfreiwillig "
            f"({(vor.error or 'ohne Meldung').strip()[:160]}).\n"
            "Bereits geänderte Dateien (Stand liegt im Worktree, NICHT neu schreiben ohne "
            "vorher zu lesen):\n" + "\n".join(f"- {d}" for d in dateien[:40]) +
            (f"\n\nSein letzter Satz war:\n{letzter}" if letzter else "") +
            "\n\nLies diese Dateien, bevor du sie erneut änderst, und mache dort weiter, "
            "statt von vorn anzufangen.")


async def _open_tasks(db: AsyncSession) -> str:
    from ..models.ticket import Issue
    rows = (
        await db.execute(
            select(Issue).where(Issue.assigned_agent.isnot(None)).order_by(Issue.updated_at.desc()).limit(20)
        )
    ).scalars().all()
    if not rows:
        return "Keine zugewiesenen Tickets offen."
    return "\n".join(f"- {i.key} [{i.agent_status}] {i.summary}" for i in rows)
