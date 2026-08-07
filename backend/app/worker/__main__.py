"""Traccoon Agenten-Worker: Redis-Consumer der task_queue mit echtem Tool-Loop.

Ersetzt den Node-Mock. Teilt das Backend-Image (`python -m app.worker`), öffnet
eine eigene SessionLocal (Postgres erlaubt Nebenläufigkeit).
"""
from __future__ import annotations

import asyncio
import faulthandler
import json
import logging
import os
import signal
import sys
import threading
import time
from urllib.parse import urlsplit

from redis.asyncio import Redis
from redis.exceptions import TimeoutError as RedisTimeoutError
from sqlalchemy import or_, select

from ..config import settings
from ..core.redis import (ACTIVE, ERGEBNIS_TTL, PREFIX, PROCESSING, PULS_TAKT, PULS_TTL,
                          QUEUE, get_flag, puls_key)
from ..db import SessionLocal
from ..models.agents import AgentDefinition
from ..models.enums import GlobalRole
from ..models.ops import Permission
from ..models.project import Project
from ..models.ticket import Comment, Issue
from ..models.user import User
from . import gitops
from .runtime import AgentDef, agent_def_from_row, run_agent
from .secrets import (
    resolve_git_token, resolve_provider_base_url,
    resolve_provider_token,
)

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger("traccoon.worker")

MAX_CONCURRENT = int(os.getenv("WORKER_CONCURRENCY", "3"))
# Auslaufzeit beim Beenden: so lange darf ein schon laufender Agent noch weiterarbeiten,
# bevor der Prozess geht. Ein Lauf kann Stunden dauern — vollständig abwarten ginge nicht,
# aber die zwei Minuten reichen für den laufenden Modellzug samt Werkzeug und dafür, dass
# seine Schrittzeilen geschrieben sind. Genau daraus baut der Nachfolger seine Übergabe
# (`runtime._abbruch_uebergabe`). Muss unter `stop_grace_period` in der compose.yml bleiben,
# sonst schlägt Docker vorher zu.
DRAIN_SEC = int(os.getenv("WORKER_DRAIN_SEC", "120"))
# Wartezeit von BLMOVE (serverseitig) und die Redis-Optionen gegen stille Hänger.
BLOCK_TIMEOUT = 5
_REDIS_KW = {"decode_responses": True, "socket_keepalive": True,
             "health_check_interval": 30, "retry_on_timeout": True}
# Wie oft ein Merge-Konflikt beim Accept an den Agenten zurückgeht, bevor an den Menschen
# eskaliert wird (Loop-Bremse gegen accept→conflict→approved→re-dispatch-Endlosschleifen).
MAX_CONFLICT_ROUNDS = int(os.getenv("MAX_CONFLICT_ROUNDS", "3"))
DEFAULT_CLAUDE_MODEL = os.getenv("DEFAULT_CLAUDE_MODEL", "claude-sonnet-4-5")
DEFAULT_CODEX_MODEL = os.getenv("DEFAULT_CODEX_MODEL", "gpt-5")

# Default-Rollen-Fähigkeiten, falls keine AgentDefinition existiert.
_DEFAULTS: dict[str, dict] = {
    "project_manager": {"can_delegate": True, "can_read_code": True, "mp": 20, "me": 40},
    "architect":       {"can_read_code": True, "mp": 20, "me": 80},
    "developer":       {"can_code": True, "mp": 10, "me": 80},
    "code_reviewer":   {"can_read_code": True, "mp": 8, "me": 30},
    "tester":          {"can_code": True, "mp": 8, "me": 40},
    "devops":          {"can_code": True, "mp": 8, "me": 40},
}

_PROMPTS: dict[str, str] = {
    "project_manager": "Du bist der Project Manager. Verstehe → plane → delegiere. Antworte in Nutzersprache.",
    "architect": "Du bist Architekt. Plane sorgfältig; Ergebnis = submit_plan mit Zielen, Schritten, betroffenen "
                 "Dateien, Akzeptanzkriterien (prüfbar), Testplan, Risiken. Ist die Aufgabe zu groß (>1 Schicht, "
                 "mehrere Teilfeatures oder >~5-8 Dateien), füge im Plan einen abhängigkeitsgeordneten Block ein: "
                 "<subtickets>[{\"summary\":\"...\",\"description\":\"...\",\"plan\":\"...\"}]</subtickets> "
                 "(jedes Sub-Ticket mit vollem plan inkl. Akzeptanzkriterien).",
    "developer": "Du bist Entwickler. Setze den Plan vollständig um, dann verifiziere per check. Editiere "
                 "chirurgisch; lösche niemals große Blöcke, um Fehler zu verstecken.",
    "code_reviewer": "Du bist Code-Reviewer. Prüfe Bugs/Security/Edge-Cases. Nur korrektur-erzwingende Befunde.",
    "tester": "Du bist Tester. Schreibe Tests (Happy Path + Edge Cases).",
    "devops": "Du bist DevOps. Build/Dependencies/CI-CD/Infra.",
}


def _default_agent_def(role: str, provider: str, model: str, mode: str) -> AgentDef:
    d = _DEFAULTS.get(role, {"me": 40, "mp": 10})
    return AgentDef(
        id=None, name=role, role=role, system_prompt=_PROMPTS.get(role, f"Du bist {role}."),
        provider=provider, model=model, token_name="", fallback=None, fallback_model="",
        fallback_token_name="", temperature=0.3, max_tokens=8192,
        max_iterations=d.get("mp", 10) if mode == "plan" else d.get("me", 40),
        can_code=d.get("can_code", False), can_read_code=d.get("can_read_code", False),
        can_delegate=d.get("can_delegate", False), web_search=False,
        allowed_tools=[], allowed_skills=[], autoload_skills=[], delegate_to=[],
    )


async def _build_tokens(db, owner_id, agent, project=None) -> tuple[dict, dict]:
    """(tokens, base_urls) je Provider aus der Agent-Auswahl: Primär (provider/token_name) +
    Fallback (fallback/fallback_token_name). Legacy-Keys claude_code/codex als Default-Rückfall.
    base_urls trägt die optionale eigene Endpoint-URL des jeweils gewählten Provider-Tokens
    (nur openai relevant); analog zu tokens durch dieselbe eff_name-Auswahl bestimmt.

    Projekt-Standard-Subscription (project.default_provider/-token_name) überschreibt den
    persönlichen Default des Nutzers — greift nur, wenn der Agent selbst keinen Token wählt."""
    proj_provider = getattr(project, "default_provider", "") or ""
    proj_name = getattr(project, "default_token_name", "") or ""

    def eff_name(provider: str, agent_name: str) -> str:
        if agent_name:
            return agent_name                       # Agent-Wahl hat Vorrang
        if proj_name and proj_provider == provider:  # sonst Projekt-Standard
            return proj_name
        return ""                                    # sonst persönlicher Default

    tokens = {
        "claude_code": await resolve_provider_token(db, owner_id, "claude_code", eff_name("claude_code", "")),
        "codex": await resolve_provider_token(db, owner_id, "codex", eff_name("codex", "")),
    }
    base_urls: dict[str, str | None] = {}
    prim_name = eff_name(agent.provider, agent.token_name)
    tokens[agent.provider] = await resolve_provider_token(db, owner_id, agent.provider, prim_name)
    base_urls[agent.provider] = await resolve_provider_base_url(db, owner_id, agent.provider, prim_name)
    if agent.fallback and agent.fallback != agent.provider:
        fb_name = eff_name(agent.fallback, agent.fallback_token_name)
        tokens[agent.fallback] = await resolve_provider_token(db, owner_id, agent.fallback, fb_name)
        base_urls[agent.fallback] = await resolve_provider_base_url(db, owner_id, agent.fallback, fb_name)
    return tokens, base_urls


async def _none():
    return None


async def _load_agent(db, role: str, project_id: int, mode: str, owner_id: int | None = None) -> AgentDef:
    # Nur die eigene Definition des Owners oder eine globale (user_id IS NULL) ziehen — nie die eines
    # fremden Users. Präzedenz: eigene vor global, projekt-scoped vor projektlos.
    row = (
        await db.execute(
            select(AgentDefinition).where(
                AgentDefinition.role == role, AgentDefinition.active.is_(True),
                or_(AgentDefinition.user_id == owner_id, AgentDefinition.user_id.is_(None))
                if owner_id is not None else AgentDefinition.user_id.is_(None),
                # Nur für DIESES Projekt scopte oder projektlose Definitionen — nie die eines
                # fremden Projekts.
                or_(AgentDefinition.project_id == project_id, AgentDefinition.project_id.is_(None)),
            )
            .order_by(AgentDefinition.user_id.is_(None),      # eigene zuerst
                      AgentDefinition.project_id.is_(None))   # projekt-scoped (dieses Projekt) zuerst
        )
    ).scalars().first()
    if row:
        d = agent_def_from_row(row, mode)
        if not d.model:
            d.model = DEFAULT_CLAUDE_MODEL if d.provider in ("claude_code", "claude") else DEFAULT_CODEX_MODEL
        return d
    prov = "claude_code"
    return _default_agent_def(role, prov, DEFAULT_CLAUDE_MODEL, mode)


async def handle(job: dict, redis: Redis) -> None:
    task_id = job["task_id"]
    kind = job.get("kind")
    if kind == "accept":
        # Ergebnis IMMER nach Redis schreiben: /complete wartet darauf und darf ein Ticket
        # nur bei sauberem Merge auf „Fertig" setzen (TRA-18).
        try:
            res = await _handle_accept(job, redis)
        except Exception as exc:  # noqa: BLE001
            log.exception("accept fehlgeschlagen")
            res = {"status": "failed", "error": str(exc)[:500]}
        await redis.set(f"{PREFIX}result:{task_id}", json.dumps(res or {"status": "failed"}), ex=ERGEBNIS_TTL)
        await redis.publish(f"{PREFIX}results", task_id)
        return
    if kind == "job":
        await _handle_job(job, redis)
        return
    if kind == "assistant":
        await _handle_assistant_task(job, redis)
        return
    if kind == "curator":
        await _handle_curator(job)
        return
    if kind:  # Infra-Task (testenv_start etc.) — später
        await redis.set(f"{PREFIX}result:{task_id}", json.dumps({"status": "failed",
                        "output": f"Infra-Task {kind} noch nicht implementiert"}), ex=ERGEBNIS_TTL)
        await redis.publish(f"{PREFIX}results", task_id)
        return

    async with SessionLocal() as db:
        issue = await db.get(Issue, job["issue_id"])
        project = await db.get(Project, job["project_id"])
        if issue is None or project is None:
            return
        mode = "plan" if job.get("phase") == "planning" else "execute"
        role = job["role"]
        # Wer arbeitet, steht auf „In Arbeit" — und zwar ab jetzt, nicht erst nach dem
        # nächsten Abgleich. Der Prozess setzt das beim Start eines Schrittes; ein Auftrag,
        # den die Reliable-Queue nach einem Neustart wiedervorlegt, kommt aber NICHT durch
        # den Graphen und liefe sonst mit einem „Warten"-Etikett (2026-08-07).
        from ..models.enums import TicketAgentStatus as _TS
        from ..services.artifacts import set_ticket_status as _set_status
        _ziel = _TS.planning if mode == "plan" else _TS.in_progress
        if issue.agent_status not in (_TS.done, _ziel):
            await _set_status(db, issue, _ziel)
        issue.agent_working = True
        await db.commit()
        owner_id = issue.assigned_by_user_id or issue.reporter_id or project.lead_user_id
        agent = await _load_agent(db, role, project.id, mode, owner_id)
        # Skill-Laden passiert jetzt zentral in run_agent (autoload + on-demand, beide Pfade).
        tokens, base_urls = await _build_tokens(db, owner_id, agent, project)

        # Git-Kontext / Workspace
        ws_root = None
        ctx = None
        if project.git_enabled:
            host = urlsplit(project.github_repo).hostname or ""
            token = await resolve_git_token(db, project.git_token_enc, owner_id, host) or ""
            wt = gitops.worktree_path(project.key, issue.key) if project.work_in_branches else None
            base_branch = project.merge_target or "main"
            # Sub-Ticket: auf dem Branch des Sammeltickets basieren (und später dorthin mergen).
            if issue.parent_ticket_id:
                umbrella = await db.get(Issue, issue.parent_ticket_id)
                if umbrella:
                    umb_branch = umbrella.branch_name or gitops.issue_branch(umbrella.key)
                    ens = gitops.GitCtx(
                        workdir=gitops.project_workdir(project.key), branch=umb_branch,
                        remote=project.github_repo, token=token,
                        main=project.merge_target or "main", enabled=True)
                    log.info("git ensure-umbrella %s: %s", umbrella.key,
                             await gitops.ensure_branch(ens, umb_branch, project.merge_target or "main"))
                    if not umbrella.branch_name:
                        umbrella.branch_name = umb_branch
                        umbrella.base_branch = project.merge_target or "main"
                        await db.commit()
                    base_branch = umb_branch
            ctx = gitops.GitCtx(
                workdir=gitops.project_workdir(project.key), branch=gitops.issue_branch(issue.key),
                remote=project.github_repo, token=token, worktree=wt, main=base_branch,
                enabled=True)
            note = await gitops.prepare(ctx)
            log.info("git prepare %s: %s", issue.key, note)
            ws_root = ctx.worktree or ctx.workdir
            issue.branch_name = ctx.branch
            issue.base_branch = ctx.main
            issue.git_base_sha = ctx.base_commit
            await db.commit()
        elif project.managed:
            ws_root = gitops.project_workdir(project.key)

        # Permissions
        perms_rows = (
            await db.execute(select(Permission).where(Permission.project_id == project.id))
        ).scalars().all()
        permissions = [{"tool": p.tool, "resource": p.resource, "action": p.action.value} for p in perms_rows]
        gate_on = bool(project.managed or permissions)

        # Kommentar-Verlauf (User-Kommentare + Agent-Rückfragen)
        crows = (
            await db.execute(select(Comment).where(Comment.issue_id == issue.id).order_by(Comment.created_at))
        ).scalars().all()
        comment_history = [
            {"label": c.author_label or ("User" if c.author_id else "Agent"),
             "role": "user" if c.author_id else "agent", "body": c.body}
            for c in crows if c.kind == "agent"
        ]

        result = await run_agent(
            db=db, agent=agent,
            issue={"id": issue.id, "key": issue.key, "summary": issue.summary,
                   "description": issue.description, "plan": issue.plan},
            project={"id": project.id, "key": project.key, "system_prompt": project.system_prompt,
                     "stack_dir": project.workspace_dir, "live_url": "",
                     "vault_moc_path": project.vault_moc_path},
            mode=mode, permissions=permissions, ws_root=ws_root, gate_on=gate_on, tokens=tokens,
            base_urls=base_urls,
            verify_command=project.verify_command, screenshot_enabled=project.screenshot_enabled,
            strict_success=await get_flag("strict_success"), owner_id=owner_id,
            testenv_url=issue.testenv_url or "",
            continuation_index=job.get("continuation_index", 0),
            continuation_hint=job.get("continuation_hint", ""),
            comment_history=comment_history, task_id=task_id,
            delegate_loader=(lambda r: _load_agent(db, r, project.id, "execute", owner_id)
                             if r in _DEFAULTS else _none()),
        )

        # Review-Gate (max 2 Korrektur-Runden) vor dem Abschluss
        if (mode == "execute" and result.status == "done" and project.review_enabled
                and ctx is not None and ws_root):
            result = await _review_gate(db, project, issue, agent, ws_root, gate_on, tokens,
                                        permissions, result, ctx, owner_id, task_id=task_id,
                                        base_urls=base_urls)

        # Agenten-Änderungen IMMER committen (nicht nur bei 'done') — sonst sitzt die Arbeit
        # bei Review-Hold/Rückfrage uncommittet im Worktree und ist nicht review-/testbar.
        merge_status = ""
        if mode == "execute" and ctx is not None:
            changes = await gitops.file_changes(ctx)
            cmsg = await gitops.commit(ctx, f"ticket {issue.key}: {issue.summary}")
            log.info("git commit %s: %s", issue.key, cmsg)
            # 0 Änderungen sichtbar machen — sonst landet ein Ticket stumm auf to_test.
            if not changes:
                db.add(Comment(
                    issue_id=issue.id, author_id=None, author_label="System", kind="internal",
                    body="⚠️ Keine Code-Änderungen vorgenommen. Der Agent hat nichts umgesetzt "
                         "(Anforderung evtl. bereits erfüllt oder nicht erkannt) — bitte prüfen."))
            from ..models.ticket import TicketFileChange
            for o in (await db.execute(select(TicketFileChange).where(
                    TicketFileChange.issue_id == issue.id))).scalars().all():
                await db.delete(o)
            for c in changes:
                db.add(TicketFileChange(issue_id=issue.id, path=c["path"], status=c["status"],
                                        additions=c["additions"], deletions=c["deletions"]))
            await db.commit()

        fp = None
        try:
            fp = await gitops.worktree_fingerprint(ws_root) if ws_root else None
        except Exception:  # noqa: BLE001
            pass

        payload = {
            "status": result.status,
            "success": result.status == "done",
            "output": result.text, "summary": result.summary or result.text[:400],
            "run_id": result.run_id, "worktree_fingerprint": fp,
            "blocker": {"kind": result.blocker_kind} if result.blocker_kind else None,
            "merge_status": merge_status,
        }
        await redis.set(f"{PREFIX}result:{task_id}", json.dumps(payload), ex=ERGEBNIS_TTL)
        await redis.publish(f"{PREFIX}results", task_id)
        await redis.publish(f"{PREFIX}events:{project.id}",
                            json.dumps({"type": "issue_update", "issue_key": issue.key}))
    log.info("verarbeitet %s → %s", task_id, result.status)


# Sicherheitsnetz für die Korrektur-Runden des Review-Gates — NICHT der normale Halt.
# Beendet wird an der Sache: bestanden, oder eine Korrektur, die am Code nichts mehr ändert
# (Stillstand). Die frühere harte 2 holte den Menschen, während es noch voranging — bei
# TRA-32 am 2026-08-07 sogar wegen eines Befunds, der aus der gekappten Diff-Anzeige stammte.
# Ein Ticket soll durchlaufen, solange es vorankommt.
REVIEW_RUNDEN = int(os.getenv("REVIEW_MAX_RUNDEN", "6"))


async def _review_gate(db, project, issue, exec_agent, ws_root, gate_on, tokens, permissions,
                       result, ctx, owner_id=None, task_id="", base_urls=None):
    """Review-Agent prüft den kumulativen Diff. <review-ok/> = bestanden. Sonst max 2
    Korrektur-Runden durch den Ausführungs-Agenten; danach hold_review.

    Die verbrauchten Runden stehen AM TICKET, nicht in dieser Schleife. Ein Zähler im
    Prozess ist nach jedem Worker-Neustart wieder null — TRA-32 lief am 2026-08-07 genau
    hinein: prüfen → korrigieren → Neustart → prüfen → korrigieren, und die Grenze, die
    den Menschen holen soll, wurde nie erreicht.
    """
    reviewer = await _load_agent(db, project.review_agent or "code_reviewer", project.id, "execute", owner_id)
    rev = None      # kein Prüflauf in dieser Runde (Budget schon verbraucht) → kein Befundtext
    vorheriger_diff: str | None = None
    for attempt in range(int(issue.review_rounds or 0), REVIEW_RUNDEN):
        diff = await gitops.diff_text(ctx)
        if not diff.strip():
            return result  # nichts geändert → nichts zu prüfen
        # Solange sich etwas bewegt, wird weitergearbeitet — die Grenze ist Stillstand, nicht
        # eine Rundenzahl. Hat die letzte Korrektur den Diff nicht angefasst, bringt die
        # nächste Runde nichts: dann holt es den Menschen, und zwar mit diesem Grund.
        if vorheriger_diff is not None and diff == vorheriger_diff:
            log.warning("review %s: Runde %d hat nichts verändert → Stillstand",
                        issue.key, attempt)
            db.add(Comment(
                issue_id=issue.id, author_id=None, author_label="Prüfer", kind="internal",
                body=("🛑 Stillstand im Review: die letzte Korrektur hat am Code nichts "
                      "geändert. Weitere Runden würden nur Tokens kosten.\n\nOffene "
                      "Befunde:\n\n" + (getattr(rev, "text", "") or "(kein Text)")[:4000])))
            await db.commit()
            from .runtime import RunResult
            return RunResult("blocked", "Review-Gate: Korrektur ohne Wirkung (Stillstand)",
                             run_id=result.run_id, blocker_kind="review")
        vorheriger_diff = diff
        rev_prompt = (
            "Prüfe den folgenden Diff strikt (Bugs, Security, Edge Cases). Antworte GENAU `<review-ok/>` "
            "(nichts sonst), wenn keine korrektur-erzwingenden Befunde vorliegen. Sonst nummeriere die "
            "Befunde (Datei/Stelle/Problem/erwartete Korrektur) als Arbeitsauftrag. Schreibe keine Dateien.\n\n"
            f"# Diff für {issue.key}: {issue.summary}\n```diff\n{diff}\n```")
        rev = await run_agent(
            db=db, agent=reviewer,
            issue={"id": issue.id, "key": issue.key, "summary": f"Review {issue.key}",
                   "description": rev_prompt, "plan": None},
            project={"id": project.id, "key": project.key, "system_prompt": "", "stack_dir": "", "live_url": ""},
            mode="execute", permissions=permissions, ws_root=ws_root, gate_on=gate_on, tokens=tokens,
            base_urls=base_urls,
            verify_command="", screenshot_enabled=False, owner_id=owner_id, task_id=task_id)
        if "<review-ok/>" in (rev.text or ""):
            log.info("review %s: bestanden (Runde %d)", issue.key, attempt + 1)
            return result
        # Ein ABGEBROCHENER Prüfer hat keine Befunde — er hat gar nicht geprüft. Ohne diese
        # Unterscheidung wurde seine Fehlermeldung als Arbeitsauftrag weitergereicht: TRA-31
        # schickte am 2026-08-07 den Entwickler los, „claude: Antwort bei max_tokens
        # abgeschnitten … max_tokens erhöhen" zu beheben. Das kostet eine der zwei
        # Korrektur-Runden, verbrennt einen vollen Lauf und endet danach im Review-Hold —
        # wegen eines Befunds, den es nie gab.
        if rev.status != "done":
            log.warning("review %s: Prüfer-Lauf %s (Runde %d) — keine Befunde, kein Auftrag",
                        issue.key, rev.status, attempt + 1)
            db.add(Comment(
                issue_id=issue.id, author_id=None, author_label="System", kind="internal",
                body=(f"⚠️ Prüfer-Lauf abgebrochen ({rev.status}): "
                      f"{(rev.text or '(ohne Meldung)')[:400]}\n\n"
                      "Der Diff ist damit UNGEPRÜFT. Das Ergebnis geht trotzdem weiter — "
                      "eine abgebrochene Prüfung ist kein Befund, und den Entwickler auf "
                      "eine Fehlermeldung anzusetzen wäre eine erfundene Aufgabe.")))
            await db.commit()
            return result
        log.info("review %s: Befunde (Runde %d von %d) → Korrektur",
                 issue.key, attempt + 1, REVIEW_RUNDEN)
        # Die Runde ist verbraucht, sobald sie beginnt — und zwar committet, damit sie einen
        # Neustart mitten in der Korrektur überlebt.
        issue.review_rounds = attempt + 1
        await db.commit()
        # Korrektur-Runde durch den Ausführungs-Agenten
        result = await run_agent(
            db=db, agent=exec_agent,
            issue={"id": issue.id, "key": issue.key, "summary": issue.summary,
                   "description": issue.description, "plan": issue.plan},
            project={"id": project.id, "key": project.key, "system_prompt": project.system_prompt,
                     "stack_dir": project.workspace_dir, "live_url": ""},
            mode="execute", permissions=permissions, ws_root=ws_root, gate_on=gate_on, tokens=tokens,
            base_urls=base_urls,
            verify_command=project.verify_command, screenshot_enabled=project.screenshot_enabled,
            strict_success=await get_flag("strict_success"), owner_id=owner_id, task_id=task_id,
            continuation_index=99, continuation_hint="REVIEW-BEFUNDE (beheben):\n" + (rev.text or ""))
        if result.status != "done":
            return result
    # Runden verbraucht und immer noch Befunde → an den Menschen. MIT den Befunden: das
    # Ticket trug bisher nur „hold: review", und wer nachsehen wollte, woran es liegt,
    # musste den Lauf in der Datenbank suchen (TRA-32 am 2026-08-07). Ein Mensch, der
    # entscheiden soll, braucht den Grund am selben Ort wie die Entscheidung.
    offene = (getattr(rev, "text", "") or "").strip()
    db.add(Comment(
        issue_id=issue.id, author_id=None, author_label="Prüfer", kind="internal",
        body=(f"🛑 Nach {REVIEW_RUNDEN} Korrektur-Runden sind noch Befunde offen — "
              "das Ticket wartet auf dich.\n\n" +
              (offene[:4000] if offene else
               "(das Korrektur-Budget war schon vor diesem Durchgang verbraucht — die "
               "Befunde stehen im vorigen Prüfer-Eintrag)") +
              "\n\nWeiterarbeiten lassen: Ticket erneut anstoßen (das Korrektur-Budget "
              "beginnt dann von vorn). Abnehmen: die Befunde bewusst überstimmen.")))
    await db.commit()
    from .runtime import RunResult
    return RunResult("blocked", f"Review-Gate: Befunde nach {REVIEW_RUNDEN} Runden offen",
                     run_id=result.run_id, blocker_kind="review")


async def _handle_accept(job: dict, redis: Redis) -> dict:
    """Bei Abnahme: Ticket-Branch → main mergen (+ push), optional Auto-Deploy einreihen.

    Liefert den Ausgang als {"status": merged|conflict|push_failed|pr_open|pr_failed|no_git|gone,
    "error"?: str} — `/complete` entscheidet daran, ob das Ticket „Fertig" werden darf.
    """
    from ..models.ops import Deployment
    async with SessionLocal() as db:
        issue = await db.get(Issue, job["issue_id"])
        project = await db.get(Project, job["project_id"])
        if issue is None or project is None:
            return {"status": "gone", "error": "Ticket oder Projekt existiert nicht mehr"}
        # Idempotenz: ein bereits gemergtes Ticket NICHT erneut mergen. Verhindert, dass
        # Duplikat-/Nachzügler-Accept-Jobs (z. B. aus Queue-Recovery) einen sauber gemergten
        # Branch erneut anfassen und in einen Scheinkonflikt laufen (Loop-Quelle).
        if issue.merge_status == "merged":
            log.info("accept %s → bereits gemerged, übersprungen", job["issue_id"])
            return {"status": "merged"}
        if project.git_enabled and issue.branch_name:
            host = urlsplit(project.github_repo).hostname or ""
            owner_id = issue.assigned_by_user_id or issue.reporter_id or project.lead_user_id
            token = await resolve_git_token(db, project.git_token_enc, owner_id, host) or ""
            # Sub-Ticket mergt in den Sammelticket-Branch, sonst in den Ziel-Branch.
            target = project.merge_target or "main"
            if issue.parent_ticket_id:
                umbrella = await db.get(Issue, issue.parent_ticket_id)
                if umbrella and umbrella.branch_name:
                    target = umbrella.branch_name
            ctx = gitops.GitCtx(
                workdir=gitops.project_workdir(project.key), branch=issue.branch_name,
                remote=project.github_repo, token=token,
                worktree=gitops.worktree_path(project.key, issue.key) if project.work_in_branches else None,
                base_commit=issue.git_base_sha, main=target, enabled=True)
            # Pre-Merge-Gate: frisches main in den Worktree; Konflikt → an den Agenten zurück
            pre = await gitops.precheck_merge(ctx)
            if pre and pre.conflict:
                issue.merge_status = "conflict"
                issue.merge_error = "Merge-Konflikt: " + ", ".join(pre.conflict_files[:8])
                issue.resolved_at = None
                issue.merge_conflict_rounds += 1
                # Loop-Bremse: konvergiert der Konflikt nicht, wird eskaliert statt endlos
                # an den Agenten zurückgereicht. Ob das „hold" oder ein neuer Anlauf heißt,
                # entscheidet der Abnahme-Prozess — hier wird nur der Befund gemeldet.
                escalate = issue.merge_conflict_rounds > MAX_CONFLICT_ROUNDS
                if escalate:
                    db.add(Comment(issue_id=issue.id, author_id=None, kind="internal",
                                   body=f"⛔ Merge-Konflikt nach {issue.merge_conflict_rounds - 1} "
                                        f"Auflösungsversuchen ungelöst — an den Menschen eskaliert. "
                                        f"Konflikt in: {', '.join(pre.conflict_files[:8])}"))
                    log.info("accept %s → Konflikt-Limit erreicht, eskaliert", job["issue_id"])
                else:
                    # Konfliktmarker in den Worktree legen, damit der Agent sie auflösen kann.
                    await gitops.setup_conflict_resolution(ctx)
                    log.info("accept %s → Konflikt (Runde %d), zurück an den Agenten",
                             job["issue_id"], issue.merge_conflict_rounds)
                await db.commit()
                await redis.publish(f"{PREFIX}events:{project.id}",
                                    json.dumps({"type": "issue_update", "issue_key": issue.key}))
                return {"status": "conflict", "error": issue.merge_error,
                        "escalate": escalate, "rounds": issue.merge_conflict_rounds}
            if project.use_pull_request and not issue.parent_ticket_id:
                # Statt zu mergen: Branch pushen, PR öffnen, Entscheidung bleibt auf GitHub.
                # (Sub-Tickets mergen immer direkt in den Sammelticket-Branch, kein PR.)
                res = await gitops.open_pull_request(
                    ctx, title=f"{issue.key}: {issue.summary}",
                    body=(issue.plan or issue.description or "")[:60000])
                if res.startswith("pr:"):
                    url = res.split(":", 1)[1]
                    issue.merge_status = "pr_open"
                    db.add(Comment(issue_id=issue.id, author_id=None, kind="agent",
                                   body=f"Pull Request geöffnet: {url}"))
                else:
                    issue.merge_status = "pr_failed"
                    issue.merge_error = res
                    db.add(Comment(issue_id=issue.id, author_id=None, kind="agent",
                                   body=f"Pull Request konnte nicht geöffnet werden: {res}"))
                await db.commit()
                await redis.publish(f"{PREFIX}events:{project.id}",
                                    json.dumps({"type": "issue_update", "issue_key": issue.key}))
                log.info("accept %s → %s", job["issue_id"], res.split(":", 1)[0])
                return ({"status": "pr_open"} if issue.merge_status == "pr_open"
                        else {"status": "pr_failed", "error": issue.merge_error})
            res = await gitops.accept(ctx)
            if res.startswith("merged:"):
                issue.merge_status = "merged"
                issue.merge_commit = res.split(":", 1)[1]
                issue.merged_into = ctx.main
                issue.merge_error = None
                issue.merge_conflict_rounds = 0   # sauber durch → Konflikt-Zähler zurücksetzen
                await gitops.remove_worktree(ctx)
            elif res.startswith("conflict:"):
                issue.merge_status = "conflict"
                issue.merge_error = res
            elif res == "push_failed":
                # Lokal gemergt, Remote-Push fehlgeschlagen → Worktree behalten, nicht deployen.
                issue.merge_status = "push_failed"
                issue.merge_error = "Push zum Remote fehlgeschlagen (Auth/Netz)."
            await db.commit()
        # Auto-Deploy NUR bei echtem Merge in den Ziel-Branch (nicht bei Sub-Tickets,
        # die in den Sammelticket-Branch mergen, und nicht bei conflict/push_failed/pr).
        if project.auto_deploy and issue.merge_status == "merged" and not issue.parent_ticket_id:
            # Tickets dürfen NICHT das Host-/Wartungsprojekt selbst deployen. Ein leerer
            # (self-zielender) stack_dir würde vom Deployer ohnehin abgelehnt und bei jedem
            # Loop-Durchlauf nur einen Deploy-Sturm erzeugen (siehe TRA-19). Der Host-Stack
            # wird ausschließlich über das explizite, idle-gegatete Wartungs-Update recreated
            # (dispatcher self_deploy, nur wenn kein Agent läuft).
            if project.workspace_dir:
                db.add(Deployment(project_id=project.id, issue_id=issue.id,
                                  stack_dir=project.workspace_dir, status="pending",
                                  source="merge"))
                await db.commit()
            else:
                log.info("accept %s: Self-/Host-Projekt — kein Ticket-Deploy "
                         "(Host-Stack nur via Wartungs-Update)", job["issue_id"])
        # Sub-Ticket fertig gemergt → nächstes geparktes Geschwister freigeben bzw.
        # Sammelticket abschließen (erst NACH dem Merge, damit Teil n+1 auf n aufbaut).
        if issue.parent_ticket_id and issue.merge_status == "merged":
            from ..services.lifecycle_flow import promote_split
            await promote_split(db, issue)
            await db.commit()
        await redis.publish(f"{PREFIX}events:{project.id}",
                            json.dumps({"type": "issue_update", "issue_key": issue.key}))
    log.info("accept %s → merge=%s deploy=%s", job["issue_id"], issue.merge_status, project.auto_deploy)
    if not (project.git_enabled and issue.branch_name):
        return {"status": "no_git"}   # Projekt ohne Git: nichts zu mergen, Abnahme ist frei
    if issue.merge_status == "merged":
        return {"status": "merged"}
    return {"status": issue.merge_status or "failed", "error": issue.merge_error}


async def _handle_job(job: dict, redis: Redis) -> None:
    """Prompt-Job: läuft über den vollen Agenten-Tool-Loop des Eigentümers (run_agent) —
    mit dessen Token, seinem Assistenten und seinen MCP-Servern (owner-gescoped).

    Eigenständig, nicht im Board (kein ws_root, keine Permission-Gates). notify_mode → Notification.
    """
    from ..models.ops import Job, JobRun
    from ..models.notification import Notification
    from .runtime import run_agent
    job_run_id = job["job_run_id"]
    async with SessionLocal() as db:
        jr = await db.get(JobRun, job_run_id)
        j = await db.get(Job, job["job_id"]) if job.get("job_id") else None
        if jr is None or j is None:
            # Früher ein stilles `return`: der Job-Run blieb für immer auf „running", ohne
            # Fehler und ohne Lauf. Passierte, wenn der Auftrag vor dem Commit eingereiht
            # wurde und ein freier Worker schneller war als die Transaktion.
            log.warning("Job-Auftrag %s ohne Datensatz (job_run=%s, job=%s) — übersprungen",
                        job.get("task_id"), job_run_id, job.get("job_id"))
            return
        # Sicherheitsnetz: script/workflow/http laufen bei ihrem Auslöser (Scheduler, API,
        # Agent-Tool) und dürfen hier nicht ankommen. Kämen sie doch, liefe der Assistent auf
        # dem Prompt-Feld eines Jobs, der gar keinen Prompt hat — lieber ein sichtbarer Fehler.
        if j.kind not in ("", "prompt"):
            jr.status = "error"
            jr.error = f"Job-Art '{j.kind}' gehört nicht in den Worker (Auslöser hat nicht verzweigt)"
            jr.finished_at = _now_dt()
            await db.commit()
            log.error("Job %s (%s) fiel in den Prompt-Pfad", j.name, j.kind)
            return
        # Eigentümer des Jobs → sein Token, sein Assistent-Agent, seine MCP-Server, seine Zustellung.
        owner_id = j.user_id
        notify_chat = j.notify_chat
        out, status, err = "", "ok", ""
        try:
            # Token-/Agent-Auflösung im try — sonst bleibt JobRun bei Fehler ewig „running".
            agent = await _load_agent(db, j.agent or "assistent", 0, "execute", owner_id)
            tokens, base_urls = await _build_tokens(db, owner_id, agent)
            if not notify_chat and owner_id:
                owner = await db.get(User, owner_id)
                notify_chat = owner.telegram_chat_id if owner else None
            # Platzhalter im Prompt aus den Job-Parametern füllen (`jobs.args` als Objekt) —
            # `last_run_at` steht hier schon auf JETZT, der vorige Lauf kommt daher aus dem
            # vorletzten JobRun. Ohne das fragte ein täglicher Digest nach „seit gerade eben".
            # Nur ERFOLGREICHE Läufe zählen: war der Job gestern kaputt, muss das Zeitfenster
            # die Lücke mitnehmen, sonst fällt ein Tag stillschweigend unter den Tisch.
            from ..services.job_params import rendere
            vorlauf = (await db.execute(
                select(JobRun.started_at).where(JobRun.job_id == j.id, JobRun.id != jr.id,
                                                JobRun.status == "ok")
                .order_by(JobRun.id.desc()).limit(1))).scalar()
            prompt_text = rendere(j.prompt, j.args, letzter_lauf=vorlauf)
            result = await run_agent(
                db=db, agent=agent,
                issue={"id": None, "key": f"job-{jr.id}", "summary": j.name,
                       "description": prompt_text, "plan": None},
                project={"id": None, "key": "", "system_prompt": "", "vault_moc_path": None},
                mode="execute", permissions=[], ws_root=None, gate_on=False, tokens=tokens,
                base_urls=base_urls, owner_id=owner_id, task_id=job["task_id"])
            out = result.summary or result.text or ""
            if result.status not in ("done",):
                status, err = "error", (result.text or result.status)
        except Exception as exc:  # noqa: BLE001
            status, err = "error", str(exc)
        jr.status = status
        jr.output = out[:20000]
        jr.error = err[:2000]
        jr.finished_at = _now_dt()
        # Benachrichtigung je notify_mode
        if j.notify_mode == "always" or (j.notify_mode == "on_output" and out) or \
                (j.notify_mode == "on_error" and status == "error"):
            title = f"Job: {j.name}" + (" fehlgeschlagen" if status == "error" else "")
            body = err if status == "error" else out
            if j.result_html:
                body = f"/digest/{jr.id}"
            db.add(Notification(kind="job", title=title, body=body[:4000], chat_id=notify_chat))
        await db.commit()
    log.info("job %s → %s", job["job_id"], status)


# Gesprächsverlauf im Chat (TRA-30): Ein Chat war bisher eine Folge voneinander unabhängiger
# Läufe — der Mensch musste sich schon innerhalb eines Gesprächs wiederholen.
#
# Das reine Zeitfenster (8 Wortwechsel / 12 h) hat den Bezug allerdings SCHLAGARTIG gekappt:
# der Mensch bezog sich auf gestern, der Assistent kannte nur die letzte Stunde. Jetzt bleiben
# die jüngsten Wortwechsel wörtlich, alles Ältere wandert in eine mitwachsende Zusammenfassung
# (`chat_summaries`) — Vorbild ist die Kontext-Kompaktierung von Hermes.
CHAT_HISTORY_MAX = 8
CHAT_HISTORY_HOURS = 12
# Wie weit zurück überhaupt noch zum selben Gespräch gezählt wird. Großzügiger als das
# wörtliche Fenster, weil Zusammengefasstes fast nichts kostet.
CHAT_MEMORY_DAYS = 14
# So viele Wortwechsel sammeln sich über dem wörtlichen Fenster an, bevor zusammengefasst
# wird. Ohne diesen Puffer liefe ab dem neunten Wortwechsel bei JEDER Nachricht ein
# Aux-Lauf mit — Wartezeit für den Menschen, ohne dass sich das Gedächtnis nennenswert ändert.
CHAT_SUMMARY_BLOCK = 4

_ZUSAMMENFASSEN = (
    "Du führst das Gedächtnis eines persönlichen Assistenten. Fasse das bisherige Gespräch so "
    "zusammen, dass er es später fortsetzen kann, ohne dass sein Mensch sich wiederholen muss.\n\n"
    "Nimm auf: was der Mensch will und entschieden hat, seine Vorlieben und Vorgaben, offene "
    "Fragen, vereinbarte nächste Schritte, konkrete Fakten (Namen, Zahlen, Pfade, IDs). Lass "
    "weg: Höflichkeiten, Wiederholungen, alles Erledigte ohne Nachwirkung.\n\n"
    "Stichpunkte, deutsch, ohne Vorrede. Halte dich kurz, aber verliere keine Zusage."
)


async def _chat_history(db, t) -> list[dict]:
    """Der Gesprächsfaden: Zusammenfassung des Älteren + die jüngsten Wortwechsel wörtlich."""
    import datetime as _dt

    from ..models.assistant import AssistantTask, ChatSummary
    agent_name = (t.meta or {}).get("agent") or "assistent"
    seit = _now_dt() - _dt.timedelta(days=CHAT_MEMORY_DAYS)
    alle = (await db.execute(
        select(AssistantTask).where(
            AssistantTask.owner_user_id == t.owner_user_id,
            AssistantTask.kind == "chat",
            AssistantTask.id != t.id,
            AssistantTask.status == "done",
            AssistantTask.created_at >= seit,
        ).order_by(AssistantTask.id))).scalars().all()
    # Fach-Agenten führen eigene Gespräche — der UniWar-Operator hat mit dem Assistenten nichts zu tun.
    alle = [r for r in alle if ((r.meta or {}).get("agent") or "assistent") == agent_name]

    def wortwechsel(r) -> list[dict]:
        meta = r.meta or {}
        raus = []
        if (frage := (meta.get("chat_text") or r.title or "").strip()):
            raus.append({"label": "Dein Mensch", "role": "user", "body": frage[:2000]})
        if (antwort := (r.result or "").strip()):
            raus.append({"label": "Du", "role": "agent", "body": antwort[:2000]})
        return raus

    summary = (await db.execute(select(ChatSummary).where(
        ChatSummary.owner_user_id == t.owner_user_id,
        ChatSummary.agent == agent_name))).scalar_one_or_none()

    # Noch nicht zusammengefasst = steht wörtlich im Verlauf. Zusammengefasst wird in Blöcken,
    # nicht bei jedem Nachrücken: sonst liefe zu JEDER Nachricht ein Aux-Lauf, sobald das
    # Gespräch einmal über acht Wortwechsel hinaus ist.
    offen = [r for r in alle if r.id > (summary.bis_task_id if summary else 0)]
    neu_zu_fassen: list = []
    if len(offen) > CHAT_HISTORY_MAX + CHAT_SUMMARY_BLOCK:
        neu_zu_fassen = offen[:-CHAT_HISTORY_MAX]
    jung = offen[-CHAT_HISTORY_MAX:] if neu_zu_fassen else offen
    if neu_zu_fassen:
        bisher = (summary.text if summary else "").strip()
        roh = "\n".join(f"{w['label']}: {w['body']}" for r in neu_zu_fassen for w in wortwechsel(r))
        auftrag = (_ZUSAMMENFASSEN
                   + ("\n\n--- Bisheriges Gedächtnis (fortschreiben, nichts verlieren) ---\n" + bisher
                      if bisher else "")
                   + "\n\n--- Neue Wortwechsel ---\n" + roh)
        from .aux import aux_chat
        agent = await _load_agent(db, agent_name, 0, "execute", t.owner_user_id)
        tokens, base_urls = await _build_tokens(db, t.owner_user_id, agent)
        text = await aux_chat(db, owner_id=t.owner_user_id, task="compression",
                              messages=[{"role": "user", "content": auftrag}],
                              agent=agent, tokens=tokens, base_urls=base_urls, max_tokens=1500)
        if text:
            if summary is None:
                summary = ChatSummary(owner_user_id=t.owner_user_id, agent=agent_name)
                db.add(summary)
            summary.text = text
            summary.bis_task_id = neu_zu_fassen[-1].id
            await db.commit()
        # Kein Ergebnis (Aux nicht erreichbar): die alte Zusammenfassung gilt weiter. Lieber ein
        # etwas veraltetes Gedächtnis als gar keins — der Faden reißt dadurch nicht.

    verlauf: list[dict] = []
    if summary and summary.text.strip():
        verlauf.append({"label": "Woran du dich erinnerst", "role": "agent",
                        "body": "# Früheres aus diesem Gespräch\n" + summary.text.strip()})
    for r in jung:
        verlauf.extend(wortwechsel(r))
    return verlauf


# Die Spielregel fürs Melden — der Lauf selbst ist keine Nachricht wert.
MELDE_REGEL = (
    "WICHTIG — Melden: Deine Abschluss-Zusammenfassung geht NICHT an deinen Menschen, sie "
    "landet nur still im Posteingang. Soll er etwas erfahren (Frist, Geldbetrag, "
    "Entscheidung, Störung, etwas das er beantworten muss), rufe `traccoon_notify_human` "
    "mit einer kurzen, konkreten Meldung. Für Erledigtes ohne Handlungsbedarf (abgelegt, "
    "vermerkt, nichts zu tun) meldest du dich NICHT."
)


async def _bezug_quelle(db, task_id) -> str:
    """Zusatzkontext zur zitierten Nachricht: worum ging es im ursprünglichen Eingang?"""
    if not task_id:
        return ""
    from ..models.assistant import AssistantTask
    quelle = await db.get(AssistantTask, int(task_id))
    if quelle is None:
        return ""
    teile = [f"Diese Nachricht gehört zu deinem Vorgang „{quelle.title}\" "
             f"({quelle.kind}, Stand {quelle.status})."]
    if quelle.result:
        teile.append(f"Was du dort zuletzt berichtet hast:\n{quelle.result[:1500]}")
    return "\n".join(teile) + "\n\n"


async def _handle_assistant_task(job: dict, redis: Redis) -> None:
    """Freigegebenes Assistent-Item (z. B. Mail) über den vollen Tool-Loop des Owners abarbeiten.

    Projektlos wie `_handle_job`: run_agent mit issue.id=None/project.id=None, Owner-Token +
    Owner-MCP-Gruppe. Der Prompt trägt die GESCHWÄRZTE Zusammenfassung + Mail-Metadaten; den
    Volltext holt sich der Assistent bei Bedarf selbst über die IMAP-Tools (die Freigabe des
    Menschen war zugleich die Freigabe für diesen Zugriff).
    """
    from ..models.assistant import AssistantTask
    from ..models.notification import Notification
    from .runtime import run_agent
    tid = job["assistant_task_id"]
    async with SessionLocal() as db:
        t = await db.get(AssistantTask, tid)
        if t is None or t.status not in ("approved",):
            return
        t.status = "running"
        await db.commit()

        owner_id = t.owner_user_id
        meta = t.meta or {}
        acc, uid = meta.get("account", ""), meta.get("uid", "")
        is_chat = t.kind == "chat"
        head = (f"Von: {meta.get('from', '')}\nBetreff: {meta.get('subject', '')}\n"
                f"Kategorie: {t.category} · Priorität: {t.priority}\n\n")
        if t.redaction == "unredacted" and t.raw_body:
            # Quelle ist per Regel als vertrauenswürdig markiert → Volltext direkt.
            content = f"Volltext (für diese Quelle freigegeben):\n{t.raw_body}\n\n"
        elif t.redacted_summary:
            content = (f"Zusammenfassung (geschwärzt):\n{t.redacted_summary}\n\n"
                       f"Der Volltext liegt im IMAP-Konto '{acc}' unter UID {uid}. Lies ihn NUR über "
                       "die imap-Tools, falls du ihn zum Handeln wirklich brauchst.\n\n")
        else:
            # Passthrough (keine Vorklassifizierung, wie im Vorläufer): der Agent liest die Mail selbst.
            content = (f"Die Mail liegt im IMAP-Konto '{acc}' unter UID {uid}. Lies sie über die "
                       "imap-Tools.\n\n")
        learned = (f"Gelernte Vorgabe deines Menschen für solche Eingänge: {t.action_hint}\n\n"
                   if t.action_hint else "")
        if is_chat:
            # Direkter Chat: die Nachricht IST der Auftrag. Traccoon steuerst du über die
            # traccoon_*-Tools (in den Rechten deines Menschen), Persönliches über deine MCP.
            prompt = (meta.get("chat_text") or t.title) + (
                f"\n\n(Kontext: gelernte Vorgabe — {t.action_hint})" if t.action_hint else "")
            # Antwort auf eine bestimmte Nachricht: sie ist der Bezug, nicht das Gespräch im
            # Allgemeinen. Ohne das bliebe „mach das" ohne Gegenstand — und der frühere
            # Eingang (Mail, Freigabe) wäre nur noch als Erinnerungsfetzen vorhanden.
            if meta.get("bezug_text"):
                quelle = await _bezug_quelle(db, meta.get("bezug_task_id"))
                prompt = (
                    "Dein Mensch antwortet DIREKT auf diese deine Nachricht:\n"
                    f"---\n{meta['bezug_text']}\n---\n"
                    + quelle +
                    "Seine Antwort darauf ist dein Auftrag — arbeite an genau dieser Sache "
                    "weiter, statt sie nur zur Kenntnis zu nehmen:\n\n" + prompt
                )
        elif meta.get("prompt"):
            # Voller Task-Prompt aus dem Webhook (portiertes Mail-Verarbeitungs-Wissen).
            prompt = meta["prompt"] + (learned if t.action_hint else "") + "\n\n" + MELDE_REGEL
        else:
            prompt = (
                "Eingang für deinen Menschen (lokal vorklassifiziert).\n" + head + content + learned +
                "Entscheide eigenständig und im Sinne deines Menschen, was zu tun ist (im Vault "
                "vermerken, einen Entwurf vorbereiten, einen Termin anlegen, ablegen …) und führe es "
                "aus. Fasse am Ende knapp zusammen, was du getan hast.\n\n" + MELDE_REGEL
            )
        # Im Chat trägt der Lauf das bisherige Gespräch mit — sonst müsste der Mensch jeden
        # Bezug in jeder Nachricht wiederholen.
        verlauf = await _chat_history(db, t) if is_chat else []
        out, status, err, run_id = "", "done", "", None
        frage_offen = False
        try:
            # Bearbeitender Agent aus dem Item (Webhook-Config), Default 'assistent'. Kein Env.
            agent = await _load_agent(db, meta.get("agent") or "assistent",
                                      0, "execute", owner_id)
            tokens, base_urls = await _build_tokens(db, owner_id, agent)
            result = await run_agent(
                db=db, agent=agent,
                issue={"id": None, "key": f"assistant-{t.id}", "summary": t.title,
                       "description": prompt, "plan": None},
                project={"id": None, "key": "", "system_prompt": "", "vault_moc_path": None},
                mode="execute", permissions=[], ws_root=None, gate_on=False, tokens=tokens,
                base_urls=base_urls, owner_id=owner_id, task_id=job["task_id"],
                comment_history=verlauf,
                history_title="# Bisheriges Gespräch (älteste Nachricht zuerst)",
                assistant_task_id=t.id)
            if result.status == "blocked" and getattr(result, "blocker_kind", None) == "assistant_perm":
                # Tool-Gate: Item wartet auf Freigabe (Status awaiting + Telegram-Karte gesetzt).
                # NICHT finalisieren — der Lauf wird nach der Entscheidung neu angestoßen.
                log.info("assistant-task %s → wartet auf Freigabe (%s)", tid, result.text)
                return
            out = result.summary or result.text or ""
            run_id = getattr(result, "run_id", None)
            if result.status == "blocked":
                # Rückfrage (ask_human) — projektlos gibt es kein Ticket, an dem sie hängen
                # könnte. Im Gespräch IST die Frage die Antwort: fertig melden, damit sie
                # den Menschen erreicht und im Verlauf (`_chat_history`) stehen bleibt.
                frage_offen = True
            elif result.status not in ("done",):
                status, err = "error", (result.text or result.status)
        except Exception as exc:  # noqa: BLE001
            status, err = "error", str(exc)

        t.status = status
        t.result = out[:20000]
        t.error = err[:2000]
        t.run_id = run_id
        t.finished_at = _now_dt()
        owner = await db.get(User, owner_id) if owner_id else None
        # Wann sich der Assistent überhaupt meldet. Voreinstellung „needed": nur wenn es
        # etwas zu wissen gibt — der Lauf selbst ist keine Nachricht wert. Sonst wäre jede
        # abgelegte Werbemail ein Telegram-Ping.
        modus = (owner.assistant_notify if owner else "needed") or "needed"
        if is_chat:
            melden = True          # eine gestellte Frage wird immer beantwortet
        elif frage_offen:
            melden = True          # der Assistent fragt zurück — sonst wartet er auf niemanden
        elif modus == "never":
            melden = False
        elif status == "error":
            melden = True          # eine Panne muss man wissen
        elif modus == "always":
            melden = True
        else:
            # needed/errors: der Assistent meldet selbst, wenn es etwas zu wissen gibt
            # (`traccoon_notify_human`) — der Abschlussbericht schweigt dann, sonst käme
            # dieselbe Sache zweimal an.
            melden = False
        if melden:
            # Wer geantwortet hat, gehört in den Titel — sonst sind Antworten des persönlichen
            # Assistenten und die eines Fach-Agenten (z. B. uniwar-operator) nicht zu unterscheiden.
            label = "🤖 Assistent" if (meta.get("agent") or "assistent") == "assistent" \
                else f"🛰 {meta['agent']}"
            title = (label if is_chat else f"{label}: {t.title}") + (
                " — Fehler" if status == "error"
                else " — Rückfrage" if frage_offen and not is_chat else "")
            db.add(Notification(kind="assistant", title=title[:200],
                                body=(err if status == "error" else out)[:4000],
                                chat_id=owner.telegram_chat_id if owner else None))
        elif not t.notified:
            # Still erledigt: das Ergebnis steht im Posteingang des Assistenten. Als
            # ungelesene Glocken-Meldung ohne chat_id wäre es Lärm, also gar nichts.
            log.info("assistant-task %s still erledigt (Modus %s)", tid, modus)
        await db.commit()
    log.info("assistant-task %s → %s", tid, status)
    # Gedächtnis-Pflege anstoßen — nach getaner Arbeit, als eigener Auftrag. `kuratiere`
    # entscheidet selbst, ob überhaupt etwas fällig ist (höchstens einmal je Tag und Notiz).
    if owner_id:
        from ..core.redis import enqueue_task
        await enqueue_task({"kind": "curator", "task_id": f"curator-{owner_id}-{tid}",
                            "owner_id": owner_id,
                            "agent_role": (meta.get("agent") or "assistent")})


async def _handle_curator(job: dict) -> None:
    """Gedächtnis aufräumen — als eigener Auftrag, nicht im Gespräch.

    Hermes stößt seinen Curator bei Untätigkeit an. Hier ist der Auslöser das Ende eines
    Assistenten-Laufs; die Arbeit selbst läuft aber getrennt, damit niemand auf sie wartet.
    Fällt sie aus, ist das folgenlos: das Gedächtnis bleibt dann eben, wie es war.
    """
    from .aux import aux_config
    from .curator import kuratiere
    from .mcp_client import mcp_session
    from .runtime import _agent_mcp, _owner_gateway
    owner_id = job.get("owner_id")
    rolle = job.get("agent_role") or "assistent"
    if not owner_id:
        return
    async with SessionLocal() as db:
        # Ohne eigenes Modell für die Gedächtnispflege bleibt sie AUS. Sonst liefe im
        # Hintergrund unbemerkt das Arbeitsmodell — teuer für Fleißarbeit, und es würde
        # ungefragt am Vault des Menschen schreiben. Die Kompaktierung darf `auto` nutzen
        # (dort ist die Alternative ein abgebrochener Lauf), das Aufräumen nicht.
        if not await aux_config(db, "curator"):
            return
        try:
            agent = await _load_agent(db, rolle, 0, "execute", owner_id)
            tokens, base_urls = await _build_tokens(db, owner_id, agent)
            gw_url, gw_token = await _owner_gateway(db, owner_id)
            async with mcp_session(agent.name, servers=await _agent_mcp(db, agent, owner_id),
                                   gateway_url=gw_url or "", gateway_token=gw_token or "") as mcp:
                berichte = await kuratiere(db, mcp, owner_id=owner_id, agent_role=rolle,
                                           agent=agent, tokens=tokens, base_urls=base_urls)
            for b in berichte:
                log.info("Curator: %s", b)
        except Exception:  # noqa: BLE001
            log.exception("Curator fehlgeschlagen (folgenlos)")


def _now_dt():
    import datetime as _dt
    return _dt.datetime.now(tz=_dt.timezone.utc)


async def heartbeat(redis: Redis) -> None:
    while True:
        try:
            await redis.set(f"{PREFIX}runner:heartbeat", int(time.time() * 1000), ex=10)
        except Exception:  # noqa: BLE001
            pass
        _loop_tick()
        await asyncio.sleep(5)


async def _puls(redis: Redis, task_id: str) -> None:
    """Lebenszeichen EINES Auftrags, solange er verarbeitet wird.

    Der Runner-Heartbeat sagt nur „ein Worker läuft" — nicht, ob DIESER Auftrag noch
    jemandem gehört. Der Wächter im Backend wartet an diesem Puls entlang und darf deshalb
    beliebig lange warten: ein Agentenlauf mit mehreren Review-Runden dauert schon mal
    Stunden, verschwinden tut er nur, wenn der Puls ausbleibt.
    """
    if not task_id:
        return
    while True:
        try:
            await redis.set(puls_key(task_id), int(time.time()), ex=PULS_TTL)
        except Exception:  # noqa: BLE001
            pass
        await asyncio.sleep(PULS_TAKT)


# ── Wächter über den Event-Loop ──────────────────────────────────────────────
# Am 2026-07-30 stand der Worker über eine Stunde: kein Heartbeat, elf Aufträge in der
# Warteschlange, keiner abgeholt — und KEINE Zeile im Log. Von außen sah der Container
# gesund aus, der Assistent schwieg einfach. Steht der Loop, hilft keine Coroutine mehr
# beim Melden; deshalb wacht hier ein echter Thread und schreibt die Stacks aller Threads
# ins Log, sobald der Loop nicht mehr tickt. Damit ist der nächste Fall diagnostizierbar,
# statt wieder nur Stille zu hinterlassen.
_LETZTER_TICK = time.monotonic()
LOOP_STALL_SEC = float(os.getenv("WORKER_STALL_SEC", "60"))
# Am 2026-07-31 hat der Wächter seine Aufgabe erfüllt und trotzdem nichts genützt: Stacks im
# Log, danach acht Stunden Stillstand bei 100 % CPU (Endlosschleife in der Kompaktierung),
# keine Telegram-Antwort. Melden allein reicht nicht. Steht der Loop so lange, ist er tot —
# dann lieber aussteigen und den Container (restart: unless-stopped) neu starten lassen.
# 0 schaltet das Beenden ab.
LOOP_KILL_SEC = float(os.getenv("WORKER_STALL_KILL_SEC", "300"))


def _loop_tick() -> None:
    global _LETZTER_TICK
    _LETZTER_TICK = time.monotonic()


def watchdog_pruefe(gemeldet: bool) -> bool:
    """Ein Durchgang des Wächters. Rückgabe: ist der Stillstand (weiterhin) gemeldet?"""
    steht_seit = time.monotonic() - _LETZTER_TICK
    if steht_seit > LOOP_STALL_SEC:
        if not gemeldet:
            log.error("Event-Loop tickt seit %.0fs nicht mehr — Thread-Stacks folgen", steht_seit)
            faulthandler.dump_traceback()   # nach stderr → Container-Log
        if LOOP_KILL_SEC and steht_seit > LOOP_KILL_SEC:
            log.error("Event-Loop steht seit %.0fs — Worker beendet sich für den Neustart", steht_seit)
            faulthandler.dump_traceback()   # letzter Stand vor dem Abgang
            sys.stderr.flush()
            sys.stdout.flush()
            os._exit(1)                     # kein sauberer Shutdown möglich: der Loop reagiert ja nicht
        return True
    if gemeldet:
        log.warning("Event-Loop läuft wieder (Stillstand %.0fs)", steht_seit)
    return False


def start_loop_watchdog() -> None:
    def lauf() -> None:
        gemeldet = False
        while True:
            time.sleep(5)
            gemeldet = watchdog_pruefe(gemeldet)

    threading.Thread(target=lauf, name="loop-watchdog", daemon=True).start()


# ── Aufräumen nach hartem Abgang ─────────────────────────────────────────────
# Wer beim Absturz lief, läuft nicht mehr — nur wusste das bisher niemand: Läufe standen
# tagelang auf `running`, Assistent-Aufgaben ebenso. Und weil der Handler nur `approved`
# annimmt, wurde die aus PROCESSING zurückgeholte Aufgabe beim Neustart stillschweigend
# verworfen. Aus einem Ausfall wurde so ein unsichtbarer Ausfall.
#
# Annahme: es läuft genau EIN Worker-Container (compose.yml kennt keine Replicas). Die
# Karenzzeit schützt trotzdem vor dem Grenzfall, dass nebenan gerade ein Lauf angelegt
# wurde — was der Wächter killt, steht ohnehin seit Minuten.
STALE_GRACE_SEC = 60


async def _lauf_abschliessen(task_id: str, grund: str) -> None:
    """Die Laufzeile eines abgebrochenen Auftrags schließen — mit der echten Ursache.

    Wer abbricht, weiß warum. Bleibt die Zeile auf „läuft" stehen, findet sie später der
    Wächter für tote Läufe und muss raten: Lauf 778 wurde am 2026-08-07 als „kein
    Lebenszeichen … Absturz beim Schreiben" beerdigt, obwohl jemand auf Stopp gedrückt
    hatte. Eine falsche Ursache ist schlimmer als keine — sie beendet die Suche.
    """
    if not task_id:
        return
    from ..models.agents import Run
    try:
        async with SessionLocal() as db:
            run = (await db.execute(select(Run).where(
                Run.task_id == task_id, Run.status == "running")
                .order_by(Run.id.desc()).limit(1))).scalars().first()
            if run is None:
                return
            run.status, run.finished_at = "failed", _now_dt()
            run.error = ((run.error or "") + grund).strip()
            await db.commit()
    except Exception:  # noqa: BLE001 — das Aufräumen darf den Abbruch nicht verschlimmern
        log.exception("Laufzeile nach Abbruch nicht geschlossen (task %s)", task_id)


async def raeume_leichen_und_melde() -> None:
    """Einmal beim Start: verwaiste Läufe/Aufgaben abschließen und den Menschen informieren."""
    from ..models.agents import Run
    from ..models.assistant import AssistantTask
    from ..models.notification import Notification
    import datetime as _dt
    grenze = _now_dt() - _dt.timedelta(seconds=STALE_GRACE_SEC)
    hinweis = "Worker-Neustart: der Lauf war beim Abbruch nicht zu Ende und wird nicht fortgesetzt."
    async with SessionLocal() as db:
        runs = (await db.execute(select(Run).where(
            Run.status == "running", Run.started_at < grenze))).scalars().all()
        for r in runs:
            r.status, r.error, r.finished_at = "failed", (r.error or "") + hinweis, _now_dt()

        tasks = (await db.execute(select(AssistantTask).where(
            AssistantTask.status == "running", AssistantTask.updated_at < grenze))).scalars().all()
        for t in tasks:
            t.status, t.error, t.finished_at = "error", (t.error or "") + hinweis, _now_dt()

        if not runs and not tasks:
            await db.commit()
            return

        # Empfänger: wem die Aufgaben gehören, plus die Admins (Läufe tragen keinen Owner).
        # Nur Konten mit Telegram — ein Ausfall, den niemand liest, ist kein Ausfall weniger,
        # und schlafende Admin-Konten mit ungelesenen Glocken zuzuschütten hilft keinem.
        empfaenger = {t.owner_user_id for t in tasks if t.owner_user_id}
        admins = (await db.execute(select(User).where(
            User.global_role == GlobalRole.admin))).scalars().all()
        empfaenger |= {u.id for u in admins}
        users = [u for u in (await db.execute(select(User).where(
            User.id.in_(empfaenger)))).scalars().all() if (u.telegram_chat_id or "").strip()]

        def _liste(ids: list[int]) -> str:
            return ", ".join(str(i) for i in ids[:10]) + (" …" if len(ids) > 10 else "")

        body = (f"Der Worker wurde neu gestartet. Abgebrochen: {len(runs)} Lauf/Läufe"
                f"{' (' + _liste([r.id for r in runs]) + ')' if runs else ''}"
                f", {len(tasks)} Assistent-Aufgabe(n)"
                f"{' (' + _liste([t.id for t in tasks]) + ')' if tasks else ''}.\n\n"
                "Diese Arbeit wird NICHT automatisch wiederholt — wenn sie noch gebraucht wird, "
                "schick sie bitte erneut.")
        for u in users:
            db.add(Notification(user_id=u.id, kind="failed",
                                title="⚠️ Worker nach Abbruch neu gestartet",
                                body=body[:4000], chat_id=u.telegram_chat_id))
        await db.commit()
    log.warning("Aufräumen nach Neustart: %d Lauf/Läufe und %d Assistent-Aufgabe(n) abgeschlossen",
                len(runs), len(tasks))


PULL_INTERVAL = 60


async def pull_loop(redis: Redis) -> None:
    """Hält die main-Checkouts aller Git-Projekte frisch (fetch + fast-forward).

    Einmalig beim Start: Reste aus PROCESSING (Job per blmove gepoppt, aber der Worker
    ist vor dem ACK abgestürzt) zurück in QUEUE schieben, damit sie nicht verloren gehen.
    """
    recovered = 0
    duplicates = 0
    seen: set[str] = set()
    while True:
        raw = await redis.rpop(PROCESSING)
        if raw is None:
            break
        try:
            tid = json.loads(raw).get("task_id")
        except Exception:  # noqa: BLE001
            tid = None  # unparsbar → behandeln wie task_id-los, nie als Duplikat verwerfen
        # Nur echte, task_id-tragende Run-Jobs deduplizieren. Bei wiederholten Restarts
        # kann dieselbe task_id mehrfach in PROCESSING liegen → nur DISTINCT zurück in QUEUE,
        # weitere Vorkommen verwerfen (nicht erneut einreihen). task_id-lose Jobs immer requeuen.
        if tid and tid in seen:
            duplicates += 1
            continue
        if tid:
            seen.add(tid)
        await redis.lpush(QUEUE, raw)
        recovered += 1
    if recovered or duplicates:
        log.info("Recovery: %d Job(s) aus PROCESSING zurück in QUEUE, %d Duplikat(e) verworfen",
                 recovered, duplicates)
    while True:
        await asyncio.sleep(PULL_INTERVAL)
        try:
            async with SessionLocal() as db:
                projects = (await db.execute(
                    select(Project).where(Project.git_enabled.is_(True)))).scalars().all()
                for project in projects:
                    if not project.github_repo:
                        continue
                    host = urlsplit(project.github_repo).hostname or ""
                    token = await resolve_git_token(db, project.git_token_enc, None, host) or ""
                    ctx = gitops.GitCtx(
                        workdir=gitops.project_workdir(project.key), branch=project.merge_target or "main",
                        remote=project.github_repo, token=token, worktree=None,
                        main=project.merge_target or "main", enabled=True)
                    if not os.path.isdir(ctx.workdir):
                        continue  # noch nie geklont — der erste Lauf erledigt das
                    note = await gitops.refresh_main(ctx)
                    if note not in ("main aktualisiert", "kein Remote"):
                        log.debug("pull %s: %s", project.key, note)
        except Exception:  # noqa: BLE001
            log.exception("pull-loop-Fehler")


# Laufende Läufe je Ticket-Key → für den kill-Kanal (Abbruch aus der UI)
RUNNING: dict[str, asyncio.Task] = {}
# Gesetzt, sobald SIGTERM/SIGINT kam: keine neuen Aufträge mehr annehmen, laufende
# auslaufen lassen. Ohne das riss jeder Deploy die Agenten mitten im Zug ab — am
# 2026-08-07 zweimal TRA-31, jeweils nach knapp 40 Zügen Arbeit.
_beenden = asyncio.Event()
# Der Spiegel in Redis (ACTIVE) kommt aus core.redis — das Backend prüft denselben Key.

# In-flight-Dedup: task_ids, die gerade verarbeitet werden. Ein Restart-Sturm kann
# über die PROCESSING→QUEUE-Recovery denselben Job (gleiche task_id) mehrfach in die
# Queue schieben; ohne diese Sperre würde der Worker (concurrency>1) ihn parallel
# fahren → RPM-Burst → HTTP 429. Zugriff nur aus dem einen Event-Loop → kein Lock nötig,
# solange Prüfen+Hinzufügen ohne await dazwischen (atomar) vor dem Start passiert.
_inflight_task_ids: set[str] = set()


async def kill_listener(redis: Redis) -> None:
    """Abonniert traccoon:kill; Payload = Ticket-Key → laufenden Lauf abbrechen."""
    pubsub = redis.pubsub()
    await pubsub.subscribe(f"{PREFIX}kill")
    async for msg in pubsub.listen():
        if msg.get("type") != "message":
            continue
        key = (msg.get("data") or "").strip()
        task = RUNNING.get(key)
        if task and not task.done():
            task.cancel()
            log.info("kill: Lauf für %s abgebrochen", key)


async def main() -> None:
    # socket_keepalive + health_check_interval: ohne sie wartet der Client auf einer
    # halb toten Verbindung endlos auf Antwort — kein Fehler, kein Timeout, kein Log.
    # Der blockierende Client braucht zusätzlich ein Socket-Limit ÜBER der BLMOVE-Zeit,
    # sonst deckt das serverseitige Timeout den Fall gar nicht ab.
    redis = Redis.from_url(settings.redis_url, **_REDIS_KW)
    blocking = Redis.from_url(settings.redis_url, socket_timeout=BLOCK_TIMEOUT + 10, **_REDIS_KW)
    killer = Redis.from_url(settings.redis_url, **_REDIS_KW)
    sem = asyncio.Semaphore(MAX_CONCURRENT)
    # Eintraege aus einem abgestuerzten Vorleben verwerfen — sonst zeigt die
    # Oberflaeche Laeufe an, die es nicht mehr gibt.
    await redis.delete(ACTIVE)
    # Vor dem ersten Job: was aus dem Vorleben noch auf `running` steht, ist tot. Aufräumen und
    # melden — sonst bleibt ein Ausfall unsichtbar (der Handler verwirft `running`-Aufgaben still).
    try:
        await raeume_leichen_und_melde()
    except Exception:  # noqa: BLE001
        log.exception("Aufräumen nach Neustart fehlgeschlagen (Worker läuft trotzdem an)")
    asyncio.create_task(heartbeat(redis))
    asyncio.create_task(kill_listener(killer))
    asyncio.create_task(pull_loop(redis))
    start_loop_watchdog()
    _signale_annehmen()
    log.info("Traccoon-Worker gestartet (concurrency=%d, Auslaufzeit %ds)",
             MAX_CONCURRENT, DRAIN_SEC)

    async def _run(job: dict, raw: str) -> None:
        key = job.get("issue_key") or job.get("task_id", "")
        async with sem:
            RUNNING[key] = asyncio.current_task()
            await redis.hset(ACTIVE, key, json.dumps({
                "issue_key": key, "task_id": job.get("task_id", ""), "role": job.get("role", ""),
                "phase": job.get("phase", ""), "project_id": job.get("project_id"),
                "started_at": time.time()}))
            # Puls: solange dieser Auftrag hier verarbeitet wird, weiß das Backend, dass er
            # lebt — egal ob er zwei Minuten oder fünf Stunden braucht. Der Wächter dort
            # wartet daran entlang, statt nach fester Zeit aufzugeben.
            puls = asyncio.create_task(_puls(redis, job.get("task_id", "")))
            try:
                await handle(job, redis)
                # Sauberer Durchlauf → ACK: den exakt gepoppten Eintrag aus PROCESSING tilgen.
                await redis.lrem(PROCESSING, 1, raw)
            except asyncio.CancelledError:
                log.info("Lauf %s abgebrochen (kill)", key)
                await redis.set(f"{PREFIX}result:{job['task_id']}", json.dumps(
                    {"status": "failed", "success": False,
                     "output": "Abgebrochen (Stopp durch Nutzer)"}), ex=ERGEBNIS_TTL)
                await redis.publish(f"{PREFIX}results", job["task_id"])
                # Die Laufzeile hier selbst schließen. Ohne das blieb sie auf „läuft" stehen,
                # und der Wächter für tote Läufe fand später eine Leiche, deren Ursache er
                # nicht kannte: Lauf 778 wurde am 2026-08-07 mit „kein Lebenszeichen …
                # Absturz beim Schreiben" beerdigt, obwohl jemand schlicht auf Stopp gedrückt
                # hatte. Wer die Ursache kennt, soll sie hinschreiben.
                await _lauf_abschliessen(job.get("task_id", ""),
                                         "Abgebrochen: Stopp über den Kill-Kanal (Knopf, "
                                         "Prozess-Schritt oder Wartungs-Update).")
                # Kein ACK: Eintrag bleibt in PROCESSING → Recovery holt ihn beim naechsten
                # Worker-Start zurueck in QUEUE (kein Datenverlust bei Abbruch/Absturz).
            except Exception as exc:  # noqa: BLE001
                log.exception("handle-Fehler")
                # WICHTIG: Ergebnis publizieren, sonst hängt der Dispatcher bis zum 1800s-Timeout
                # und das Ticket bleibt agent_working=True (blockiert einen Runner-Slot).
                tid = job.get("task_id")
                if tid:
                    await redis.set(f"{PREFIX}result:{tid}", json.dumps(
                        {"status": "failed", "success": False,
                         "output": f"Interner Worker-Fehler: {exc}"[:500]}), ex=ERGEBNIS_TTL)
                    await redis.publish(f"{PREFIX}results", tid)
                # Kein ACK: Eintrag bleibt in PROCESSING → Recovery holt ihn beim naechsten
                # Worker-Start zurueck in QUEUE, statt ihn hier stillschweigend zu verlieren.
            finally:
                RUNNING.pop(key, None)
                puls.cancel()
                await redis.hdel(ACTIVE, key)
                # Puls sofort löschen statt auslaufen lassen: das Ergebnis liegt bereits in
                # Redis, und ein Nachhall würde den Wächter unnötig weiterwarten lassen.
                await redis.delete(puls_key(job.get("task_id", "")))
                # In-flight-Sperre zuverlässig lösen — auch bei Exception/Abbruch,
                # sonst bliebe die task_id für immer als „läuft bereits" markiert.
                _inflight_task_ids.discard(job.get("task_id"))

    while not _beenden.is_set():
        try:
            # blmove statt brpop: der Job wandert atomar von QUEUE nach PROCESSING statt
            # nur zu verschwinden — stirbt der Worker vor dem ACK, findet ihn die
            # Recovery in pull_loop() beim naechsten Start wieder (Reliable Queue).
            raw = await blocking.blmove(QUEUE, PROCESSING, timeout=BLOCK_TIMEOUT,
                                        src="RIGHT", dest="LEFT")
            _loop_tick()
            if not raw:
                continue
            if _beenden.is_set():
                # Zwischen blmove und hier kam das Signal: den Auftrag ZURÜCK in die
                # Warteschlange legen, statt ihn in einem sterbenden Prozess zu starten.
                # Er soll gleich vom neuen Worker geholt werden, nicht erst von dessen
                # Recovery aus PROCESSING.
                await redis.lrem(PROCESSING, 1, raw)
                await redis.rpush(QUEUE, raw)
                break
            job = json.loads(raw)
            # In-flight-Dedup nach task_id: läuft derselbe Dispatch schon (z.B. durch die
            # Restart-Recovery doppelt eingereiht), diesen Job aus PROCESSING tilgen und NICHT
            # verarbeiten. Prüfen+Hinzufügen ohne await dazwischen → atomar im Event-Loop.
            # task_id-lose Jobs (falls je welche) laufen unverändert, werden nie geblockt.
            task_id = job.get("task_id")
            if task_id:
                if task_id in _inflight_task_ids:
                    await redis.lrem(PROCESSING, 1, raw)
                    log.warning("Duplikat-Job task_id=%s verworfen (läuft bereits)", task_id)
                    continue
                _inflight_task_ids.add(task_id)
            asyncio.create_task(_run(job, raw))
        except RedisTimeoutError:
            continue      # Socket-Limit griff vor der Antwort — nichts Besonderes
        except Exception:  # noqa: BLE001
            log.exception("loop-Fehler")
            await asyncio.sleep(1)

    await _auslaufen()


def _signale_annehmen() -> None:
    """SIGTERM/SIGINT nicht mehr mitten in die Arbeit schlagen lassen.

    Docker schickt beim Neustart erst SIGTERM und tötet nach der Gnadenfrist. Ohne Handler
    starb der Prozess sofort — mitsamt jedem Agenten, der gerade dachte. Die Wiedervorlage
    rettet den Auftrag, nicht das Gespräch: am 2026-08-07 kostete das TRA-31 zweimal knapp
    40 Züge Arbeit.
    """
    schleife = asyncio.get_running_loop()
    for sig in (signal.SIGTERM, signal.SIGINT):
        try:
            schleife.add_signal_handler(sig, _beenden.set)
        except NotImplementedError:      # Windows/Test-Umgebung: dann eben wie bisher
            pass


async def _auslaufen() -> None:
    """Laufende Aufträge zu Ende bringen, so weit die Auslaufzeit reicht."""
    laufend = [t for t in RUNNING.values() if not t.done()]
    if not laufend:
        log.info("Worker beendet sich — nichts läuft mehr")
        return
    log.info("Worker beendet sich: %d Lauf/Läufe aktiv, warte bis zu %d s "
             "(neue Aufträge werden nicht mehr angenommen)", len(laufend), DRAIN_SEC)
    _fertig, offen = await asyncio.wait(laufend, timeout=DRAIN_SEC)
    if offen:
        # Kein Abbruch von Hand: die Aufträge stehen noch in PROCESSING, die Recovery des
        # nächsten Workers holt sie zurück, und der Nachfolger bekommt aus den Schrittzeilen
        # seine Übergabe. Docker beendet den Prozess gleich ohnehin.
        log.warning("Auslaufzeit vorbei, %d Lauf/Läufe unfertig — sie werden neu eingereiht",
                    len(offen))
    else:
        log.info("Alle Läufe sauber beendet")


if __name__ == "__main__":
    asyncio.run(main())
