"""Agenten-Tool-Loop (Port aus nexus/agent/runtime.py, auf SQLAlchemy+Traccoon).

mode=plan|execute. Eingebaute Tools: fs_read/list/write/edit, check, deploy,
screenshot, ask_human, submit_plan, continue_later, open_tasks, delegate.
Permission-Laufzeit-Gate, Build-Gate, max_iterations-Verhalten wie in nexus.
"""
from __future__ import annotations

import asyncio
import base64
import datetime as dt
import json
import logging
import os
from dataclasses import dataclass, field
from typing import Any

import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..models.agents import AgentDefinition, Run, RunStep
from ..models.ticket import Blocker, Comment
from . import codegraph as _codegraph
from . import gitops as _gitops
from . import perms
from .mcp_client import mcp_session
from .providers.base import ProviderError
from .providers.router import router

log = logging.getLogger("traccoon.runtime")

_FS_MAX_READ = 30000
MAX_BUILD_GATE = 3
MAX_DELEGATION_DEPTH = 2

DEPLOYER_URL = os.getenv("DEPLOYER_URL", "http://deployer:8661")
SHOTTER_URL = os.getenv("SHOTTER_URL", "http://shotter:8700")


def _now() -> dt.datetime:
    return dt.datetime.now(tz=dt.timezone.utc)


# ---------- Tool-Schemas (Port) ----------

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


# ---------- FS-Tools ----------

def _fs_resolve(root: str, rel: str) -> str:
    full = os.path.realpath(os.path.join(root, rel or "."))
    rootr = os.path.realpath(root)
    if full != rootr and not full.startswith(rootr + os.sep):
        raise ValueError("Pfad außerhalb des Projekt-Workspace")
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


async def _do_deploy(db: AsyncSession, issue_id: int, project_id: int, stack_dir: str,
                     worktree: str | None, check_only: bool = False) -> str:
    """Deployment einreihen (deployments-Tabelle) und auf das Ergebnis des Deployer-Sidecars warten."""
    from ..models.nexus import Deployment
    dep = Deployment(issue_id=issue_id, project_id=project_id, stack_dir=stack_dir,
                     worktree=worktree or "", check_only=check_only,
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

    def tool_allowed(self, name: str) -> bool:
        # Leere Whitelist = alle geladenen Tools erlaubt (Registry-Tools sind schon durch die
        # Agent-Instanzen gescopt, Gateway durch die User-Gruppe). Gesetzt = Glob-Feinfilter.
        from fnmatch import fnmatch
        pats = self.allowed_tools or []
        if not pats:
            return True
        return any(fnmatch(name, p) for p in pats)


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
                     model: str, parent_run_id: int | None, continuation_index: int) -> int:
    run = Run(issue_id=issue_id, agent=agent, phase=phase, provider=provider, model=model,
              status="running", parent_run_id=parent_run_id, continuation_index=continuation_index)
    db.add(run)
    await db.commit()
    await db.refresh(run)
    return run.id


async def _add_step(db: AsyncSession, run_id: int, seq: int, role: str, tool: str | None, content: str) -> None:
    db.add(RunStep(run_id=run_id, seq=seq, role=role, tool_name=tool, content=content[:8000]))
    await db.commit()


async def _end_run(db: AsyncSession, run_id: int, status: str, summary: str = "", error: str = "",
                   iterations: int = 0, wt_fp: str | None = None,
                   in_tok: int = 0, out_tok: int = 0) -> None:
    from ..models.agents import CostEntry
    from ..models.nexus import ProviderModel
    from ..models.ticket import Issue
    run = await db.get(Run, run_id)
    if run:
        run.status = status
        run.summary = summary[:4000] if summary else run.summary
        run.error = error[:4000] if error else run.error
        run.iterations = iterations
        run.worktree_fingerprint = wt_fp
        run.input_tokens = in_tok
        run.output_tokens = out_tok
        run.last_text = summary[:2000] if summary else run.last_text
        run.finished_at = _now()
        # Kosten aus Modellpreisen (falls im Katalog)
        cost = 0.0
        if in_tok or out_tok:
            pm = (
                await db.execute(
                    select(ProviderModel).where(ProviderModel.provider == run.provider,
                                                ProviderModel.model == run.model)
                )
            ).scalar_one_or_none()
            if pm:
                cost = in_tok / 1e6 * pm.price_input + out_tok / 1e6 * pm.price_output
            run.cost_usd = cost
            project_id = (
                await db.execute(select(Issue.project_id).where(Issue.id == run.issue_id))
            ).scalar_one_or_none()
            db.add(CostEntry(run_id=run.id, issue_id=run.issue_id, agent=run.agent,
                             provider=run.provider, model=run.model, input_tokens=in_tok,
                             output_tokens=out_tok, cost_usd=cost, project_id=project_id))
        await db.commit()


async def _add_comment(db: AsyncSession, issue_id: int, label: str, body: str) -> None:
    db.add(Comment(issue_id=issue_id, author_id=None, author_label=label, body=body, kind="agent"))
    await db.commit()


# ---------- Hauptschleife ----------

def _build_system_prompt(agent: AgentDef) -> str:
    heute = dt.datetime.now().strftime("%A, %Y-%m-%d %H:%M")
    parts = [agent.system_prompt or f"Du bist {agent.role}.",
             f"Aktuelles Datum/Zeit: {heute}.",
             "Arbeite den Auftrag eigenständig ab. Nutze Tools, wenn nötig. Bist du fertig, antworte mit "
             "einer kurzen Zusammenfassung OHNE Tool-Call. Frage nur bei echten Blockern mit `ask_human`."]
    return "\n\n".join(parts)


async def _owner_gateway(db: AsyncSession, owner_id: int | None) -> tuple[str | None, str | None]:
    """MCPJungle-Gruppen-Endpoint + Token des Owners (harte Per-User-Trennung).

    Kein MCP-Config beim User → (None, None) → kein Gateway (nur Registry-Server).
    Fällt NICHT auf den globalen Gateway zurück, damit ein User nur seine Server sieht.
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
    """McpServer-Zeile → {name, url, headers}. stdio/ohne url → None (übersprungen)."""
    from ..core.security import decrypt_secret

    if r.transport not in ("http", "sse") or not r.url:
        log.info("MCP-Server %s übersprungen (transport=%s)", r.name, r.transport)
        return None
    headers = dict(r.headers or {})
    if r.env_enc:
        try:
            headers.update(json.loads(decrypt_secret(r.env_enc)))
        except Exception:  # noqa: BLE001
            log.warning("MCP-Server %s: env konnte nicht entschlüsselt werden", r.name)
    if extra_headers:
        headers.update(extra_headers)
    return {"name": r.name, "url": r.url, "headers": headers}


async def _agent_mcp(db: AsyncSession, agent: AgentDef, owner_id: int | None = None) -> list[dict]:
    """Registry-MCP-Server für DIESEN Agenten:
    - agent-eigene Instanzen (Server + ausgefüllte Variablen als Header),
    - plus globale/eigene Server OHNE Variablen-Schema (Zero-Config), abwärtskompatibel.
    Server MIT Variablen ohne Instanz werden nicht geladen (brauchen Konfiguration)."""
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
                    log.warning("MCP-Instanz %s: values nicht entschlüsselbar", inst.id)
            spec = _server_spec(srv, extra_headers=values)  # Variablen → Header
            if spec:
                out.append(spec)
                seen.add(srv.id if srv.id is not None else srv.name)

    # 2) Zero-Config-Server (kein Variablen-Schema) — global + eigene
    # Instanz hat Vorrang (bringt Header/Werte mit) → hier bereits erfasste Server überspringen,
    # sonst landet derselbe Server doppelt in der Spec-Liste (doppelte Tools im Prompt).
    zc = select(McpServer).where(McpServer.enabled.is_(True))
    zc = zc.where(or_(McpServer.user_id.is_(None), McpServer.user_id == owner_id)
                  if owner_id is not None else McpServer.user_id.is_(None))
    for r in (await db.execute(zc)).scalars().all():
        if r.variables:   # braucht Konfiguration → nur via Instanz
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
    """(Volltext der autoload-Skills, Menü der übrigen verfügbaren Skills)."""
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


async def run_agent(*, db: AsyncSession, agent: AgentDef, issue: dict, project: dict,
                    mode: str = "execute", permissions: list[dict] | None = None,
                    ws_root: str | None = None, gate_on: bool = False,
                    tokens: dict[str, str | None] | None = None, verify_command: str = "",
                    strict_success: bool = False, owner_id: int | None = None,
                    screenshot_enabled: bool = False, testenv_url: str = "",
                    continuation_index: int = 0, continuation_hint: str = "",
                    comment_history: list[dict] | None = None,
                    parent_run_id: int | None = None,
                    depth: int = 0, delegate_loader=None) -> RunResult:
    permissions = permissions or []
    tokens = tokens or {}
    issue_id = issue["id"]

    run_id = await _start_run(db, issue_id, agent.name, mode, agent.provider,
                              agent.model or agent.provider, parent_run_id, continuation_index)
    seq = 0

    async def log(role: str, tool: str | None, content: str) -> None:
        nonlocal seq
        seq += 1
        await _add_step(db, run_id, seq, role, tool, content)

    messages: list[dict[str, Any]] = [
        {"role": "system", "content": _build_system_prompt(agent)},
        {"role": "user", "content": f"# Auftrag: {issue['summary']}\n\n{issue.get('description') or ''}".strip()},
    ]
    # Skills: autoload → Volltext; verfügbare (nicht-auto) → Menü + load_skill-Tool.
    autoload_text, skill_menu = await _agent_skills(db, agent)
    if autoload_text:
        messages.append({"role": "system", "content": autoload_text})
    if skill_menu:
        messages.append({"role": "system", "content": skill_menu})
    if project.get("system_prompt"):
        messages.append({"role": "system", "content": project["system_prompt"]})
    if mode == "plan" and issue.get("plan"):
        messages.append({"role": "user", "content":
                         "# Bestehender Plan (überarbeite ihn anhand der Kommentare)\n\n" + issue["plan"]})
    if continuation_index > 0 and continuation_hint:
        messages.append({"role": "system", "content":
            f"## Fortsetzung (Runde {continuation_index})\nWorktree-Stand ist erhalten. Letzter Stand:\n"
            f"{continuation_hint}\nArbeite direkt weiter, prüfe den Build-Status, schließe offene Schritte ab."})
    if comment_history:
        thread = "\n".join(f"- **{c['label']}** ({c['role']}): {c['body']}" for c in comment_history)
        messages.append({"role": "user", "content":
                         "# Kommentar-Verlauf (Rückfragen & Antworten)\n" + thread +
                         "\n\nBerücksichtige besonders die Antworten des Nutzers (role=user)."})

    gw_url, gw_token = await _owner_gateway(db, owner_id)
    # Leerer String (nicht None) → KEIN Gateway; kein Rückfall auf globalen Gateway (harte Trennung).
    try:
        async with mcp_session(agent.name, servers=await _agent_mcp(db, agent, owner_id),
                               gateway_url=gw_url or "", gateway_token=gw_token or "") as mcp:
            mcp_tools = await mcp.list_tools()
            openai_tools = [t.to_openai() for t in mcp_tools if agent.tool_allowed(t.name)]
            openai_tools.append(ASK_HUMAN_TOOL)
            if skill_menu:  # es gibt verfügbare, nicht-auto Skills → Nachlade-Tool anbieten
                openai_tools.append(LOAD_SKILL_TOOL)
            if mode != "plan":
                openai_tools.append(CONTINUE_LATER_TOOL)
                openai_tools.append(OPEN_TASKS_TOOL)
                if agent.can_delegate and delegate_loader is not None and depth < MAX_DELEGATION_DEPTH:
                    openai_tools.append(_delegate_tool(agent.delegate_to))
            if ws_root and (agent.can_code or agent.can_read_code):
                openai_tools += [FS_READ_TOOL, FS_LIST_TOOL]
                if await _codegraph.available():
                    openai_tools.append(CODEGRAPH_TOOL)
                if screenshot_enabled:
                    openai_tools.append(SCREENSHOT_TOOL)
                if mode != "plan" and agent.can_code:
                    openai_tools += [FS_WRITE_TOOL, FS_EDIT_TOOL, CHECK_TOOL, DEPLOY_TOOL]
                    messages.append({"role": "system", "content": CODE_WORKFLOW})
            if mode == "plan":
                openai_tools.append(SUBMIT_PLAN_TOOL)

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
                    pass  # MCP nicht konfiguriert → kein Vault-Kontext

            last_text = ""
            empties = 0
            build_gate_fails = 0
            in_tok = out_tok = 0
            for iteration in range(1, agent.max_iterations + 1):
                if iteration == max(2, agent.max_iterations - 2):
                    messages.append({"role": "system", "content":
                        "⚠️ Du näherst dich dem Iterations-Limit. Wenn du NICHT unmittelbar vor dem Abschluss "
                        "stehst: `continue_later` mit Zusammenfassung. Nur bei echter Blockade: `ask_human`."})
                try:
                    resp = await router.chat(provider=agent.provider, model=agent.model, messages=messages,
                                             tools=openai_tools, temperature=agent.temperature,
                                             max_tokens=agent.max_tokens, fallback=agent.fallback,
                                             fallback_model=agent.fallback_model,
                                             web_search=agent.web_search, tokens=tokens)
                except ProviderError as exc:
                    await log("system", None, f"Provider-Fehler: {exc}")
                    await _end_run(db, run_id, "failed", error=str(exc), iterations=iteration)
                    return RunResult("failed", str(exc), iteration, run_id=run_id)

                in_tok += int(resp.usage.get("input_tokens", 0) or 0)
                out_tok += int(resp.usage.get("output_tokens", 0) or 0)
                await log("assistant", None, resp.text or "(Tool-Call)")
                if resp.text:
                    last_text = resp.text

                if not resp.tool_calls:
                    if (resp.text or "").strip():
                        if (agent.can_code and mode != "plan" and ws_root
                                and strict_success and not verify_command):
                            # Strenge Abnahme ohne Prüfbefehl: Erfolg wäre nicht belegbar.
                            err = ("Strenge Abnahme ist aktiv, aber das Projekt hat keinen verify_command. "
                                   "Ohne grünen Prüflauf gilt der Lauf nicht als erfolgreich.")
                            await _end_run(db, run_id, "failed", error=err, iterations=iteration,
                                           in_tok=in_tok, out_tok=out_tok)
                            return RunResult("failed", err, iteration, run_id=run_id)
                        if agent.can_code and mode != "plan" and ws_root and verify_command:
                            verdict = await _do_check(ws_root, verify_command)
                            if verdict.startswith("❌"):
                                build_gate_fails += 1
                                if build_gate_fails > MAX_BUILD_GATE:
                                    await _end_run(db, run_id, "loop_exhausted", error=verdict,
                                                   iterations=iteration, in_tok=in_tok, out_tok=out_tok)
                                    return RunResult("loop_exhausted", verdict, iteration, run_id=run_id)
                                messages.append({"role": "system", "content":
                                    "⛔ ABSCHLUSS BLOCKIERT: Build ist ROT. Behebe die Ursache (nichts weglöschen) "
                                    "und arbeite weiter:\n\n" + verdict})
                                continue
                        await _end_run(db, run_id, "success", summary=resp.text, iterations=iteration,
                                       in_tok=in_tok, out_tok=out_tok)
                        return RunResult("done", resp.text, iteration, summary=resp.text, run_id=run_id)
                    empties += 1
                    if empties >= 2:
                        await _end_run(db, run_id, "failed", error="Leere Modell-Antwort.", iterations=iteration)
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
                        db.add(Blocker(issue_id=issue_id, run_id=run_id, question=question))
                        await db.commit()
                        await _add_comment(db, issue_id, agent.name, question)
                        await _end_run(db, run_id, "blocked", summary=question, iterations=iteration,
                                       in_tok=in_tok, out_tok=out_tok)
                        return RunResult("blocked", question, iteration, run_id=run_id, blocker_kind="ask_human")

                    if call.name == "continue_later":
                        s = (call.arguments.get("summary") or "").strip()
                        fp = await _gitops.worktree_fingerprint(ws_root) if ws_root else None
                        await _end_run(db, run_id, "loop_exhausted", summary=s, iterations=iteration,
                                       wt_fp=fp, in_tok=in_tok, out_tok=out_tok)
                        return RunResult("loop_exhausted", s, iteration, run_id=run_id)

                    if call.name == "submit_plan" and mode == "plan":
                        plan = (call.arguments.get("plan") or "").strip()
                        if not plan:
                            messages.append({"role": "tool", "tool_call_id": call.id, "name": call.name,
                                             "content": "Plan war leer – vollständigen Plan einreichen."})
                            continue
                        psum = (call.arguments.get("summary") or "").strip()
                        await _end_run(db, run_id, "planned", summary="Plan erstellt", iterations=iteration,
                                       in_tok=in_tok, out_tok=out_tok)
                        return RunResult("planned", plan, iteration, summary=psum, run_id=run_id)

                    # Permission-Gate für mutierende externe Tools
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
                                               iterations=iteration, in_tok=in_tok, out_tok=out_tok)
                                return RunResult("blocked", f"Berechtigung: {call.name}", iteration,
                                                 run_id=run_id, blocker_kind="permission")

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
                                gate_on=gate_on, tokens=tokens, verify_command=verify_command,
                                strict_success=strict_success, owner_id=owner_id,
                                screenshot_enabled=screenshot_enabled, testenv_url=testenv_url,
                                depth=depth + 1, delegate_loader=delegate_loader, parent_run_id=run_id)
                            if sub.status == "blocked":
                                # Sub-Agent blockiert → Rückfrage an den Menschen weiterreichen
                                await _end_run(db, run_id, "blocked", summary=sub.text, iterations=iteration,
                                               in_tok=in_tok, out_tok=out_tok)
                                return RunResult("blocked", sub.text, iteration, run_id=run_id,
                                                 blocker_kind=sub.blocker_kind or "question")
                            result = f"[Sub-Agent {sub_role} → {sub.status}]\n{sub.text[:2000]}"
                    elif call.name in FS_TOOL_NAMES:
                        result = _fs_dispatch(call.name, ws_root, call.arguments)
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
                    elif not agent.tool_allowed(call.name):
                        result = f"FEHLER: Tool '{call.name}' ist für diesen Agenten nicht erlaubt."
                    else:
                        try:
                            result = await mcp.call(call.name, call.arguments)
                        except Exception as exc:  # noqa: BLE001
                            result = f"TOOL-FEHLER: {exc}"

                    if isinstance(result, list):
                        await log("tool", call.name, "(Bild/Block-Ergebnis)")
                        messages.append({"role": "tool", "tool_call_id": call.id, "name": call.name,
                                         "content": result})
                    else:
                        await log("tool", call.name,
                                  f"args={json.dumps(call.arguments, ensure_ascii=False)[:400]}\n→ {result[:2000]}")
                        messages.append({"role": "tool", "tool_call_id": call.id, "name": call.name,
                                         "content": result[:8000]})

            exhausted = "Iterations-Limit erreicht.\n\nLetzter Stand:\n" + (last_text or "(kein Text)")
            fp = await _gitops.worktree_fingerprint(ws_root) if ws_root else None
            await _end_run(db, run_id, "loop_exhausted", summary=exhausted, iterations=agent.max_iterations,
                           wt_fp=fp, in_tok=in_tok, out_tok=out_tok)
            return RunResult("loop_exhausted", exhausted, agent.max_iterations, run_id=run_id)

    except Exception as exc:  # noqa: BLE001
        log.exception("run_agent(%s) Laufzeitfehler", agent.name)
        await _end_run(db, run_id, "failed", error=str(exc))
        return RunResult("failed", str(exc), 0, run_id=run_id)


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
