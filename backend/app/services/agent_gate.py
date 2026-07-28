"""Torwächter vor jedem Agentenlauf — Zeitfenster, Nutzer-Limits und Runaway-Bremse.

Diese Regeln sind **Policy, kein Graph**: sie gelten für jeden Agentenlauf, egal wie der
Prozess gezeichnet ist. Ein Prozess-Designer kann sie also nicht versehentlich (oder
absichtlich) wegklicken — genau das war die Lehre aus ABC-19 (41 Läufe / 11,9 Mio
Input-Tokens, weil die Cap-Prüfung nur an einer Stelle hing).

Herkunft: 1:1 aus `dispatcher._gate_ok` und dem Runaway-/Warn-Block in `dispatcher._process`
herausgelöst, damit Workflow-Engine und (Rest-)Dispatcher dieselbe Wahrheit benutzen.
"""
from __future__ import annotations

import datetime as dt
import logging
from dataclasses import dataclass

from sqlalchemy import func, select
from zoneinfo import ZoneInfo

from ..core.redis import get_flag, get_user_flag
from ..models.agents import Run
from ..models.enums import HoldReason
from ..models.ticket import Comment, Issue
from ..models.user import User

log = logging.getLogger("agent_gate")

TZ = ZoneInfo("Europe/Berlin")

MAX_CONCURRENT = 3            # global gleichzeitig laufende Agenten
MAX_CONTINUATIONS = 30        # Fortsetzungen je Ticket
# Harte Runaway-Bremse pro Ticket, UNABHÄNGIG von continuation_count. Beim Erreichen →
# hold (Mensch), statt weiter Tokens zu verbrennen.
MAX_RUNS_PER_TICKET = 30
MAX_INPUT_TOKENS_PER_TICKET = 8_000_000
# Frühwarnung unter dem Hard-Cap: einmal pro Ticket eine Kostenwarnung, ohne zu blockieren.
WARN_INPUT_TOKENS_PER_TICKET = 3_000_000


@dataclass
class GateVerdict:
    """Ergebnis der Torprüfung.

    ok=True      → Lauf darf starten.
    hold=True    → dauerhafter Stopp (Runaway-Cap); der Aufrufer setzt das Ticket auf hold.
    sonst        → nur JETZT nicht (Zeitfenster/Limit); später erneut versuchen.
    """
    ok: bool
    reason: str = ""
    hold: bool = False
    hold_reason: HoldReason | None = None
    detail: str = ""


OK = GateVerdict(ok=True)


def _now() -> dt.datetime:
    return dt.datetime.now(tz=dt.timezone.utc)


async def system_paused() -> str:
    """Globale Bremsen: Not-Aus und laufendes Wartungs-Update. '' = frei."""
    if await get_flag("update_pending") or await get_flag("update_in_progress"):
        return "update"
    if await get_flag("global_pause"):
        return "pause"
    return ""


def owner_of(issue: Issue) -> int | None:
    """Wessen Kontingent/Token der Lauf belastet: wer den Agenten zugewiesen hat."""
    return issue.assigned_by_user_id or issue.reporter_id


async def schedule_ok(db, issue: Issue) -> GateVerdict:
    """Zeitliche Tore: geplanter Start, Feierabend des Eigentümers, Nacht-Fenster."""
    if issue.start_at and issue.start_at > _now():
        return GateVerdict(False, "start_at", detail=f"geplant für {issue.start_at:%d.%m. %H:%M}")
    owner_id = owner_of(issue)
    if await get_user_flag("shift_end", owner_id):
        return GateVerdict(False, "shift_end", detail="Feierabend des Eigentümers")
    if issue.night_task:
        user = await db.get(User, owner_id) if owner_id else None
        if user and not user.night_override:
            now = dt.datetime.now(TZ)
            if now.weekday() not in (user.night_days or [0, 1, 2, 3, 4, 5, 6]):
                return GateVerdict(False, "night", detail="außerhalb der Nacht-Wochentage")
            s, e, h = user.night_start_hour, user.night_end_hour, now.hour
            in_window = (s <= h < e) if s < e else (h >= s or h < e)
            if not in_window:
                return GateVerdict(False, "night", detail=f"Nacht-Fenster {s}–{e} Uhr")
    return OK


async def runner_slot_free(db, issue: Issue) -> GateVerdict:
    """Pro-Nutzer-Limit (`User.max_runners`) und globale Obergrenze gleichzeitiger Läufe.

    Gezählt wird `Issue.agent_working` — dieselbe Marke, die Engine und Monitor nutzen.
    """
    running = (await db.execute(select(Issue).where(Issue.agent_working.is_(True)))).scalars().all()
    running = [r for r in running if r.id != issue.id]
    if len(running) >= MAX_CONCURRENT:
        return GateVerdict(False, "runners", detail=f"{len(running)} Läufe global aktiv")
    owner_id = owner_of(issue)
    mine = sum(1 for r in running if (r.assigned_by_user_id or r.reporter_id) == owner_id)
    owner = await db.get(User, owner_id) if owner_id else None
    cap = owner.max_runners if owner else MAX_CONCURRENT
    if mine >= cap:
        return GateVerdict(False, "runners", detail=f"{mine}/{cap} eigene Läufe aktiv")
    return OK


async def cap_ok(db, issue: Issue) -> GateVerdict:
    """Runaway-Bremse je Ticket + einmalige Kosten-Frühwarnung.

    Cap-Fenster: ist `cap_baseline_run_id` gesetzt (letzte Plan-Freigabe), zählen nur Läufe
    danach — alte Fehlversuche belasten legitime Neu-Arbeit nicht. Der Aufrufer committet.
    """
    cond = [Run.issue_id == issue.id]
    if issue.cap_baseline_run_id:
        cond.append(Run.id > issue.cap_baseline_run_id)
    agg = (await db.execute(
        select(func.count(Run.id), func.coalesce(func.sum(Run.input_tokens), 0)).where(*cond)
    )).one()
    run_count, in_tok = int(agg[0]), int(agg[1] or 0)

    if run_count >= MAX_RUNS_PER_TICKET or in_tok >= MAX_INPUT_TOKENS_PER_TICKET:
        log.warning("Ticket %s: Runaway-Cap erreicht (%d Läufe / %d Input-Tokens) → hold",
                    issue.key, run_count, in_tok)
        return GateVerdict(False, "cap", hold=True, hold_reason=HoldReason.cap,
                           detail=f"{run_count} Läufe / {in_tok} Input-Tokens")

    if in_tok >= WARN_INPUT_TOKENS_PER_TICKET:
        warned = (await db.execute(
            select(Comment.id).where(Comment.issue_id == issue.id,
                                     Comment.kind == "cost_warn").limit(1)
        )).first()
        if warned is None:
            text = (f"⚠️ Kostenwarnung {issue.key}: {run_count} Läufe / {in_tok} Input-Tokens. "
                    f"Hard-Cap bei {MAX_RUNS_PER_TICKET} Läufen / "
                    f"{MAX_INPUT_TOKENS_PER_TICKET} Tokens → hold.")
            db.add(Comment(issue_id=issue.id, author_id=None, author_label="System",
                           body=text, kind="cost_warn"))
            from .notify import notify_issue
            await notify_issue(db, issue, "cost_warn", f"{issue.key}: Kostenwarnung", text)
            log.warning("Ticket %s: Kostenwarnung (%d Läufe / %d Input-Tokens)",
                        issue.key, run_count, in_tok)
    return OK


async def check(db, issue: Issue) -> GateVerdict:
    """Vollständige Torprüfung vor dem Einreihen eines Agentenlaufs."""
    paused = await system_paused()
    if paused:
        return GateVerdict(False, paused, detail="System pausiert (Not-Aus/Wartung)")
    verdict = await cap_ok(db, issue)      # zuerst: kann dauerhaft stoppen
    if not verdict.ok:
        return verdict
    verdict = await schedule_ok(db, issue)
    if not verdict.ok:
        return verdict
    return await runner_slot_free(db, issue)
