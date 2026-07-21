"""PM-Chat-Orchestrierung (Read-only): PM plant + delegiert per <tickets>-Ops.

Läuft im Backend (Orchestrierung, keine Code-Ausführung). Legt Tickets an / weist
Agenten zu; die eigentliche Arbeit übernimmt der Worker über den Dispatcher.
"""
from __future__ import annotations

import datetime as dt
import json
import logging
import re

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..core.redis import publish_event
from ..models.chat import Message
from ..models.enums import TicketAgentStatus
from ..models.project import Project
from ..models.ticket import Issue, IssueCounter, IssueType, WorkflowStatus
from ..models.user import SYSTEM_USER_ID
from ..worker.providers.router import router
from ..worker.secrets import resolve_provider_token

log = logging.getLogger("traccoon.pm")
MAX_ROUNDS = 10
HISTORY_LIMIT = 20
# Ab dieser Gesamtlänge wird der Verlauf gekürzt, damit lange Chats nicht ins Kontextlimit laufen.
COMPACT_CHARS = 24000
KEEP_RECENT = 6

PM_SYSTEM = (
    "Du bist der Project Manager (PM) von Traccoon. Du bist der einzige KI-Ansprechpartner des Nutzers. "
    "Verstehe die Anfrage, plane, und DELEGIERE die Arbeit, indem du Tickets anlegst und Agenten zuweist.\n\n"
    "Verfügbare Agenten-Rollen: architect (plant), developer (setzt um), code_reviewer, tester, devops.\n\n"
    "Um Tickets anzulegen/zuzuweisen, gib EINEN JSON-Block aus:\n"
    "<tickets>\n[{\"op\":\"create\",\"summary\":\"...\",\"description\":\"...\",\"assign\":\"developer\"},\n"
    " {\"op\":\"assign\",\"key\":\"ABC-1\",\"agent\":\"architect\"}]\n</tickets>\n\n"
    "`op:create` legt ein Ticket an (optional `assign` = Agenten-Rolle, die es sofort übernimmt). "
    "`op:assign` weist einem bestehenden Ticket (per key) einen Agenten zu. "
    "Antworte dem Nutzer zusätzlich in normaler Sprache. Wenn alles delegiert/erledigt ist, schreibe <done/>.\n"
    "Du kannst Tickets NICHT selbst freigeben oder abnehmen — das macht der Mensch.\n"
    "Du hast mehrere Runden Zeit: lege erst die Tickets an, prüfe im nächsten Zug das Board und ergänze, "
    "was fehlt. Schreibe <done/> erst, wenn nichts mehr offen ist."
)


def _now() -> dt.datetime:
    return dt.datetime.now(tz=dt.timezone.utc)


async def _board_context(db: AsyncSession, project_id: int) -> str:
    rows = (
        await db.execute(select(Issue).where(Issue.project_id == project_id)
                         .order_by(Issue.updated_at.desc()).limit(20))
    ).scalars().all()
    if not rows:
        return "Board ist leer."
    return "\n".join(f"- {i.key} [{i.agent_status or i.priority}] {i.summary}" for i in rows)


async def _create_ticket(db: AsyncSession, project: Project, summary: str, description: str,
                         assign: str | None, by_user: int) -> Issue:
    t = (await db.execute(select(IssueType).where(IssueType.project_id == project.id)
                          .order_by(IssueType.order))).scalars().first()
    s = (await db.execute(select(WorkflowStatus).where(WorkflowStatus.project_id == project.id)
                          .order_by(WorkflowStatus.order))).scalars().first()
    counter = (await db.execute(select(IssueCounter).where(IssueCounter.project_id == project.id)
                                .with_for_update())).scalar_one()
    counter.last_number += 1
    n = counter.last_number
    issue = Issue(project_id=project.id, number=n, key=f"{project.key}-{n}", type_id=t.id,
                  status_id=s.id, summary=summary[:500], description=description,
                  reporter_id=by_user or SYSTEM_USER_ID, rank=f"{n:08d}")
    if assign:
        issue.assigned_agent = assign
        issue.assigned_by_user_id = by_user
        issue.assigned_at = _now()
        issue.agent_status = TicketAgentStatus.planning
    db.add(issue)
    await db.flush()
    return issue


def _parse_ops(text: str) -> tuple[str, list[dict], bool]:
    """<tickets> ist der Vertrag; <tasks> wird als Synonym akzeptiert, damit ein
    Modell, das die andere Schreibweise wählt, nicht stillschweigend nichts bewirkt."""
    done = "<done/>" in text
    ops: list[dict] = []
    for tag in ("tickets", "tasks"):
        m = re.search(rf"<{tag}>\s*(\[.*?\])\s*</{tag}>", text, re.DOTALL)
        if not m:
            continue
        try:
            ops.extend(json.loads(m.group(1)))
        except json.JSONDecodeError:
            log.warning("PM: %s-Block ist kein gültiges JSON", tag)
    clean = re.sub(r"<(tickets|tasks)>.*?</\1>", "", text, flags=re.DOTALL)
    clean = clean.replace("<done/>", "").strip()
    return clean, ops, done


def _compact(convo: list[dict]) -> list[dict]:
    """Langen Verlauf kürzen: älteren Teil zu einer Notiz zusammenfassen, Jüngstes behalten."""
    total = sum(len(m["content"]) for m in convo)
    if total <= COMPACT_CHARS or len(convo) <= KEEP_RECENT:
        return convo
    old, recent = convo[:-KEEP_RECENT], convo[-KEEP_RECENT:]
    summary = " | ".join(f"{m['role']}: {m['content'][:200]}" for m in old)[:4000]
    return [{"role": "user", "content": f"[Gekürzter Verlauf der bisherigen Unterhaltung]\n{summary}"}, *recent]


async def run_pm_chat(db: AsyncSession, project_id: int, user_id: int, text: str) -> None:
    project = await db.get(Project, project_id)
    if project is None:
        return
    # Zweite Prüfung des KI-Rechts: der WS-Handler cacht es beim Verbinden, und der PM
    # weist hier Agenten zu — die Zuweisung darf nie ohne ai_assign passieren.
    from ..api.deps import build_access
    from ..models.user import User
    user = await db.get(User, user_id)
    if user is None or not (await build_access(project, user, db)).ai_assign:
        log.warning("PM-Chat ohne KI-Recht abgewiesen (user=%s, projekt=%s)", user_id, project_id)
        await publish_event(project_id, {"type": "pm_chat", "role": "system",
                                         "content": "KI-Recht (ai_assign) erforderlich."})
        return
    db.add(Message(project_id=project_id, user_id=user_id, role="user", author_label="User", content=text))
    await db.commit()

    # Claude-Token auflösen: Projekt-Standard-Subscription überschreibt den persönlichen
    # Default (nur wenn sie für claude_code gesetzt ist). Rückfall auf Alt-Feld/System-Secret.
    pm_token_name = project.default_token_name if project.default_provider == "claude_code" else ""
    token = await resolve_provider_token(db, user_id, "claude_code", pm_token_name)
    tokens = {"claude_code": token}

    hist = (await db.execute(select(Message).where(Message.project_id == project_id)
                             .order_by(Message.id.desc()).limit(HISTORY_LIMIT))).scalars().all()
    hist = list(reversed(hist))
    convo = [{"role": "assistant" if m.role in ("pm", "assistant") else "user", "content": m.content}
             for m in hist if m.content]

    for rnd in range(MAX_ROUNDS):
        board = await _board_context(db, project_id)
        convo = _compact(convo)
        messages = [{"role": "system", "content": PM_SYSTEM},
                    {"role": "system", "content": f"# Board\n{board}"}, *convo]
        try:
            resp = await router.chat(provider="claude_code", model="claude-sonnet-4-5",
                                     messages=messages, temperature=0.3, max_tokens=4096, tokens=tokens)
        except Exception as exc:  # noqa: BLE001
            db.add(Message(project_id=project_id, role="system", author_label="System",
                           content=f"PM-Fehler: {exc}"))
            await db.commit()
            await publish_event(project_id, {"type": "pm_chat", "role": "system", "content": f"PM-Fehler: {exc}"})
            return

        clean, ops, done = _parse_ops(resp.text)
        created_keys = []
        for op in ops:
            try:
                if op.get("op") == "create":
                    iss = await _create_ticket(db, project, op.get("summary", "Aufgabe"),
                                               op.get("description", ""), op.get("assign"), user_id)
                    created_keys.append(iss.key)
                elif op.get("op") == "assign":
                    iss = (await db.execute(select(Issue).where(Issue.key == op.get("key", ""),
                                                                Issue.project_id == project_id))).scalar_one_or_none()
                    if iss:
                        iss.assigned_agent = op.get("agent", "project_manager")
                        iss.assigned_by_user_id = user_id
                        iss.assigned_at = _now()
                        if iss.agent_status is None:
                            iss.agent_status = TicketAgentStatus.planning
                        created_keys.append(iss.key)
            except Exception:  # noqa: BLE001
                log.exception("PM-Op fehlgeschlagen")
        pm_text = clean + (f"\n\n🎫 {', '.join(created_keys)}" if created_keys else "")
        db.add(Message(project_id=project_id, role="pm", author_label="PM", content=pm_text, round=rnd))
        await db.commit()
        convo.append({"role": "assistant", "content": resp.text})
        await publish_event(project_id, {"type": "pm_chat", "role": "pm", "content": pm_text})
        if done:
            break
        if not ops:
            # Runde 0 ohne Ops: einmal nachfassen, damit reines Zurückplaudern nicht als Abschluss zählt.
            if rnd == 0:
                convo.append({"role": "user", "content":
                              "Wenn daraus Arbeit folgt, lege jetzt die Tickets im <tickets>-Block an "
                              "und weise Agenten zu. Wenn nichts zu tun ist, antworte mit <done/>."})
                continue
            break
