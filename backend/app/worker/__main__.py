"""Traccoon Agenten-Worker: Redis-Consumer der task_queue mit echtem Tool-Loop.

Ersetzt den Node-Mock. Teilt das Backend-Image (`python -m app.worker`), öffnet
eine eigene SessionLocal (Postgres erlaubt Nebenläufigkeit).
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import time
from urllib.parse import urlsplit

from redis.asyncio import Redis
from sqlalchemy import or_, select

from ..config import settings
from ..core.redis import PREFIX, PROCESSING, QUEUE, get_flag
from ..db import SessionLocal
from ..models.agents import AgentDefinition
from ..models.nexus import Permission
from ..models.project import Project
from ..models.ticket import Comment, Issue
from ..models.user import User
from . import gitops
from .runtime import AgentDef, agent_def_from_row, run_agent
from .secrets import (
    resolve_claude_token, resolve_codex_token, resolve_git_token, resolve_provider_token,
)

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger("traccoon.worker")

MAX_CONCURRENT = int(os.getenv("WORKER_CONCURRENCY", "3"))
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


async def _build_tokens(db, owner_id, agent, project=None) -> dict:
    """Token-Dict je Provider aus der Agent-Auswahl: Primär (provider/token_name) +
    Fallback (fallback/fallback_token_name). Legacy-Keys claude_code/codex als Default-Rückfall.

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
    tokens[agent.provider] = await resolve_provider_token(
        db, owner_id, agent.provider, eff_name(agent.provider, agent.token_name))
    if agent.fallback and agent.fallback != agent.provider:
        tokens[agent.fallback] = await resolve_provider_token(
            db, owner_id, agent.fallback, eff_name(agent.fallback, agent.fallback_token_name))
    return tokens


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
        await _handle_accept(job, redis)
        return
    if kind == "job":
        await _handle_job(job, redis)
        return
    if kind:  # Infra-Task (testenv_start etc.) — später
        await redis.set(f"{PREFIX}result:{task_id}", json.dumps({"status": "failed",
                        "output": f"Infra-Task {kind} noch nicht implementiert"}), ex=3600)
        await redis.publish(f"{PREFIX}results", task_id)
        return

    async with SessionLocal() as db:
        issue = await db.get(Issue, job["issue_id"])
        project = await db.get(Project, job["project_id"])
        if issue is None or project is None:
            return
        mode = "plan" if job.get("phase") == "planning" else "execute"
        role = job["role"]
        owner_id = issue.assigned_by_user_id or issue.reporter_id or project.lead_user_id
        agent = await _load_agent(db, role, project.id, mode, owner_id)
        # Skill-Laden passiert jetzt zentral in run_agent (autoload + on-demand, beide Pfade).
        tokens = await _build_tokens(db, owner_id, agent, project)

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
            verify_command=project.verify_command, screenshot_enabled=project.screenshot_enabled,
            strict_success=await get_flag("strict_success"), owner_id=owner_id,
            testenv_url=issue.testenv_url or "",
            continuation_index=job.get("continuation_index", 0),
            continuation_hint=job.get("continuation_hint", ""),
            comment_history=comment_history,
            delegate_loader=(lambda r: _load_agent(db, r, project.id, "execute", owner_id)
                             if r in _DEFAULTS else _none()),
        )

        # Review-Gate (max 2 Korrektur-Runden) vor dem Abschluss
        if (mode == "execute" and result.status == "done" and project.review_enabled
                and ctx is not None and ws_root):
            result = await _review_gate(db, project, issue, agent, ws_root, gate_on, tokens,
                                        permissions, result, ctx, owner_id)

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
        await redis.set(f"{PREFIX}result:{task_id}", json.dumps(payload), ex=3600)
        await redis.publish(f"{PREFIX}results", task_id)
        await redis.publish(f"{PREFIX}events:{project.id}",
                            json.dumps({"type": "issue_update", "issue_key": issue.key}))
    log.info("verarbeitet %s → %s", task_id, result.status)


async def _review_gate(db, project, issue, exec_agent, ws_root, gate_on, tokens, permissions,
                       result, ctx, owner_id=None):
    """Review-Agent prüft den kumulativen Diff. <review-ok/> = bestanden. Sonst max 2
    Korrektur-Runden durch den Ausführungs-Agenten; danach hold_review."""
    reviewer = await _load_agent(db, project.review_agent or "code_reviewer", project.id, "execute", owner_id)
    for attempt in range(2):
        diff = await gitops.diff_text(ctx)
        if not diff.strip():
            return result  # nichts geändert → nichts zu prüfen
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
            verify_command="", screenshot_enabled=False, owner_id=owner_id)
        if "<review-ok/>" in (rev.text or ""):
            log.info("review %s: bestanden (Runde %d)", issue.key, attempt + 1)
            return result
        log.info("review %s: Befunde (Runde %d) → Korrektur", issue.key, attempt + 1)
        # Korrektur-Runde durch den Ausführungs-Agenten
        result = await run_agent(
            db=db, agent=exec_agent,
            issue={"id": issue.id, "key": issue.key, "summary": issue.summary,
                   "description": issue.description, "plan": issue.plan},
            project={"id": project.id, "key": project.key, "system_prompt": project.system_prompt,
                     "stack_dir": project.workspace_dir, "live_url": ""},
            mode="execute", permissions=permissions, ws_root=ws_root, gate_on=gate_on, tokens=tokens,
            verify_command=project.verify_command, screenshot_enabled=project.screenshot_enabled,
            strict_success=await get_flag("strict_success"), owner_id=owner_id,
            continuation_index=99, continuation_hint="REVIEW-BEFUNDE (beheben):\n" + (rev.text or ""))
        if result.status != "done":
            return result
    # nach 2 Runden immer noch Befunde → an den Menschen
    from .runtime import RunResult
    return RunResult("blocked", "Review-Gate: Befunde nach 2 Runden offen", run_id=result.run_id,
                     blocker_kind="review")


async def _handle_accept(job: dict, redis: Redis) -> None:
    """Bei Abnahme: Ticket-Branch → main mergen (+ push), optional Auto-Deploy einreihen."""
    from ..models.nexus import Deployment
    async with SessionLocal() as db:
        issue = await db.get(Issue, job["issue_id"])
        project = await db.get(Project, job["project_id"])
        if issue is None or project is None:
            return
        # Idempotenz: ein bereits gemergtes Ticket NICHT erneut mergen. Verhindert, dass
        # Duplikat-/Nachzügler-Accept-Jobs (z. B. aus Queue-Recovery) einen sauber gemergten
        # Branch erneut anfassen und in einen Scheinkonflikt laufen (Loop-Quelle).
        if issue.merge_status == "merged":
            log.info("accept %s → bereits gemerged, übersprungen", job["issue_id"])
            return
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
                from ..models.enums import HoldReason as _HR, TicketAgentStatus as _TS
                issue.merge_status = "conflict"
                issue.merge_error = "Merge-Konflikt: " + ", ".join(pre.conflict_files[:8])
                issue.resolved_at = None
                issue.merge_conflict_rounds += 1
                if issue.merge_conflict_rounds > MAX_CONFLICT_ROUNDS:
                    # Loop-Bremse: der Konflikt konvergiert nicht (Agent löst ihn wiederholt
                    # nicht) → NICHT endlos re-dispatchen, sondern an den Menschen eskalieren.
                    issue.agent_status = _TS.hold
                    issue.hold_reason = _HR.merge
                    db.add(Comment(issue_id=issue.id, author_id=None, kind="internal",
                                   body=f"⛔ Merge-Konflikt nach {issue.merge_conflict_rounds - 1} "
                                        f"Auflösungsversuchen ungelöst — an den Menschen eskaliert. "
                                        f"Konflikt in: {', '.join(pre.conflict_files[:8])}"))
                    log.info("accept %s → Konflikt-Limit erreicht, hold (Mensch)", job["issue_id"])
                else:
                    await gitops.setup_conflict_resolution(ctx)
                    issue.agent_status = _TS.approved   # Continuation löst die Marker
                    issue.continuation_count += 1
                    log.info("accept %s → Konflikt (Runde %d), zurück an Agenten",
                             job["issue_id"], issue.merge_conflict_rounds)
                await db.commit()
                await redis.publish(f"{PREFIX}events:{project.id}",
                                    json.dumps({"type": "issue_update", "issue_key": issue.key}))
                return
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
                return
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
                                  stack_dir=project.workspace_dir, status="pending"))
                await db.commit()
            else:
                log.info("accept %s: Self-/Host-Projekt — kein Ticket-Deploy "
                         "(Host-Stack nur via Wartungs-Update)", job["issue_id"])
        # Sub-Ticket fertig gemergt → nächstes geparktes Geschwister freigeben bzw.
        # Sammelticket abschließen (erst NACH dem Merge, damit Teil n+1 auf n aufbaut).
        if issue.parent_ticket_id:
            from ..services.dispatcher import _promote_split
            await _promote_split(db, issue, project)
            await db.commit()
        await redis.publish(f"{PREFIX}events:{project.id}",
                            json.dumps({"type": "issue_update", "issue_key": issue.key}))
    log.info("accept %s → merge=%s deploy=%s", job["issue_id"], issue.merge_status, project.auto_deploy)


async def _handle_job(job: dict, redis: Redis) -> None:
    """Prompt-Job: läuft über den vollen Agenten-Tool-Loop des Eigentümers (run_agent) —
    mit dessen Token, seinem Assistenten und seinen MCP-Servern (owner-gescoped).

    Eigenständig, nicht im Board (kein ws_root, keine Permission-Gates). notify_mode → Notification.
    """
    from ..models.nexus import Job, JobRun
    from ..models.notification import Notification
    from .runtime import run_agent
    job_run_id = job["job_run_id"]
    async with SessionLocal() as db:
        jr = await db.get(JobRun, job_run_id)
        j = await db.get(Job, job["job_id"]) if job.get("job_id") else None
        if jr is None or j is None:
            return
        # Eigentümer des Jobs → sein Token, sein Assistent-Agent, seine MCP-Server, seine Zustellung.
        owner_id = j.user_id
        notify_chat = j.notify_chat
        out, status, err = "", "ok", ""
        try:
            # Token-/Agent-Auflösung im try — sonst bleibt JobRun bei Fehler ewig „running".
            agent = await _load_agent(db, j.agent or "assistent", 0, "execute", owner_id)
            tokens = await _build_tokens(db, owner_id, agent)
            if not notify_chat and owner_id:
                owner = await db.get(User, owner_id)
                notify_chat = owner.telegram_chat_id if owner else None
            result = await run_agent(
                db=db, agent=agent,
                issue={"id": None, "key": f"job-{jr.id}", "summary": j.name,
                       "description": j.prompt, "plan": None},
                project={"id": None, "key": "", "system_prompt": "", "vault_moc_path": None},
                mode="execute", permissions=[], ws_root=None, gate_on=False, tokens=tokens,
                owner_id=owner_id)
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


def _now_dt():
    import datetime as _dt
    return _dt.datetime.now(tz=_dt.timezone.utc)


async def heartbeat(redis: Redis) -> None:
    while True:
        try:
            await redis.set(f"{PREFIX}runner:heartbeat", int(time.time() * 1000), ex=10)
        except Exception:  # noqa: BLE001
            pass
        await asyncio.sleep(5)


PULL_INTERVAL = 60


async def pull_loop(redis: Redis) -> None:
    """Hält die main-Checkouts aller Git-Projekte frisch (fetch + fast-forward).

    Einmalig beim Start: Reste aus PROCESSING (Job per blmove gepoppt, aber der Worker
    ist vor dem ACK abgestürzt) zurück in QUEUE schieben, damit sie nicht verloren gehen.
    """
    recovered = 0
    while await redis.lmove(PROCESSING, QUEUE, "RIGHT", "LEFT"):
        recovered += 1
    if recovered:
        log.info("Recovery: %d Job(s) aus PROCESSING zurück in QUEUE geschoben", recovered)
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
# Spiegel derselben Information in Redis, damit das Backend sie anzeigen kann.
ACTIVE = f"{PREFIX}active_processes"


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
    redis = Redis.from_url(settings.redis_url, decode_responses=True)
    blocking = Redis.from_url(settings.redis_url, decode_responses=True)
    killer = Redis.from_url(settings.redis_url, decode_responses=True)
    sem = asyncio.Semaphore(MAX_CONCURRENT)
    # Eintraege aus einem abgestuerzten Vorleben verwerfen — sonst zeigt die
    # Oberflaeche Laeufe an, die es nicht mehr gibt.
    await redis.delete(ACTIVE)
    asyncio.create_task(heartbeat(redis))
    asyncio.create_task(kill_listener(killer))
    asyncio.create_task(pull_loop(redis))
    log.info("Traccoon-Worker gestartet (concurrency=%d)", MAX_CONCURRENT)

    async def _run(job: dict, raw: str) -> None:
        key = job.get("issue_key") or job.get("task_id", "")
        async with sem:
            RUNNING[key] = asyncio.current_task()
            await redis.hset(ACTIVE, key, json.dumps({
                "issue_key": key, "task_id": job.get("task_id", ""), "role": job.get("role", ""),
                "phase": job.get("phase", ""), "project_id": job.get("project_id"),
                "started_at": time.time()}))
            try:
                await handle(job, redis)
                # Sauberer Durchlauf → ACK: den exakt gepoppten Eintrag aus PROCESSING tilgen.
                await redis.lrem(PROCESSING, 1, raw)
            except asyncio.CancelledError:
                log.info("Lauf %s abgebrochen (kill)", key)
                await redis.set(f"{PREFIX}result:{job['task_id']}", json.dumps(
                    {"status": "failed", "success": False, "output": "Abgebrochen (Stopp durch Nutzer)"}), ex=3600)
                await redis.publish(f"{PREFIX}results", job["task_id"])
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
                         "output": f"Interner Worker-Fehler: {exc}"[:500]}), ex=3600)
                    await redis.publish(f"{PREFIX}results", tid)
                # Kein ACK: Eintrag bleibt in PROCESSING → Recovery holt ihn beim naechsten
                # Worker-Start zurueck in QUEUE, statt ihn hier stillschweigend zu verlieren.
            finally:
                RUNNING.pop(key, None)
                await redis.hdel(ACTIVE, key)

    while True:
        try:
            # blmove statt brpop: der Job wandert atomar von QUEUE nach PROCESSING statt
            # nur zu verschwinden — stirbt der Worker vor dem ACK, findet ihn die
            # Recovery in pull_loop() beim naechsten Start wieder (Reliable Queue).
            raw = await blocking.blmove(QUEUE, PROCESSING, timeout=5, src="RIGHT", dest="LEFT")
            if not raw:
                continue
            job = json.loads(raw)
            asyncio.create_task(_run(job, raw))
        except Exception:  # noqa: BLE001
            log.exception("loop-Fehler")
            await asyncio.sleep(1)


if __name__ == "__main__":
    asyncio.run(main())
