"""Büro (office) — die Lese-API: Sessionlisten, Ereignis-Schnappschuss, Kosten.

Eine **Session** ist ein Laufbaum, nicht ein Lauf und nicht ein Projekt. Ein einzelner
`Run` wäre zu klein — Planung, Ausführung, jede Fortsetzung und der Review-Agent sind
eine zusammenhängende Geschichte, und ein delegierter Unteragent säße in seinem eigenen
leeren Büro. Ein Projekt wäre zu groß zum Zurückspulen; das Projekt **ist** die Liste der
Sessions. Zwei Adressformen:

    issue:{issue_id}   jeder Lauf mit dieser issue_id — der Raum eines Tickets
    run:{root_run_id}  der Baum unter einem Lauf ohne Ticket (Job, Assistent)

Der Pfad heißt deshalb `/sessions/{kind}/{ref}` und nicht `/sessions/{sid}`: ein
literaler `:` in einem Pfadsegment ist zwar erlaubt, wird aber von Proxys, Clients und
Testwerkzeugen unterschiedlich kodiert. Zwei Segmente sind billiger als diese Diskussion.

**Die Baumschließung kostet zwei Abfragen, keine rekursive CTE.** Die Tiefe ist durch
`worker/runtime.MAX_DELEGATION_DEPTH` gedeckelt, also reichen zwei begrenzte
`WHERE parent_run_id IN (…)` — und die laufen unter SQLite (Tests) wie unter Postgres
identisch, während eine `WITH RECURSIVE` beide Dialekte auseinanderziehen würde.
Für `issue:` entfällt selbst das: ein delegierter Unterlauf erbt die `issue_id` seines
Elternlaufs (`worker/runtime.py`, `run_agent(issue=…)`), der Index `ix_runs_issue_started`
deckt den ganzen Baum also in EINER Abfrage ab.

**Altdaten sind kein Sonderfall, sondern der Normalfall am ersten Tag.** Läufe von vor
der Instrumentierung haben `kind=''`-Zeilen und keine `run_start`/`run_end`-Zeilen. Der
Lesepfad geht deshalb immer durch `services.office.step_events` (die den Altpfad
kennt) und ergänzt fehlende Grenzen über `run_boundary_events`. Von Welle B profitiert
dieselbe Route danach ohne eine Zeile Änderung.

**Unautorisiert ist 404, nie 403.** `build_access` macht das im ganzen Repo so
(`deps.py:165-166`); eine neue Fläche, die 403 sagt, verriete die Existenz fremder
Tickets. Bei Ereignis- und Kostenabruf wird die Berechtigung aus der **Session** abgeleitet
(Projekt/Eigentümer des Wurzellaufs), nicht aus dem Pfad — der Pfad trägt keine Projekt-ID,
und ihn eine tragen zu lassen hieße, dem Client die Autorisierung zu überlassen.
"""
from __future__ import annotations

import datetime as dt

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import and_, case, func, or_, select, true
from sqlalchemy.ext.asyncio import AsyncSession

from ..db import get_session
from ..models.agents import CostEntry, Run, RunStep
from ..models.enums import GlobalRole, ProjectRole
from ..models.project import Project
from ..models.ticket import Issue
from ..models.user import User
from ..services.office import (
    EVENT_CAP_DEFAULT, EVENT_CAP_MAX, EVENT_VERSION, LIVE_WINDOW_MS, SEQ_SLOTS,
    PriceTable, RunCtx, run_boundary_events, session_id, session_seen_event, step_events,
)
from .deps import Access, build_access, get_current_user, get_project_access
# Die erlaubte Projektmenge kommt aus DERSELBEN Funktion wie beim Live-Socket, und die
# ist ihrerseits `api/projects.py:74-91` (zwei Queries + `build_access_bulk`, keine Runde
# je Projekt). Es soll genau eine Definition von „darf sehen" geben, nicht drei, die
# irgendwann auseinanderlaufen.
from .office_ws import compute_acl

router = APIRouter(tags=["office"])

SESSION_LIMIT_DEFAULT = 50
SESSION_LIMIT_MAX = 200
SINCE_HOURS_DEFAULT = 24 * 7      # eine Woche — das Büro ist Kurzzeitgedächtnis, kein Archiv
SINCE_HOURS_MAX = 24 * 365

# Wie viele Läufe für eine Sessionliste betrachtet werden. Die Grenze gilt für Sessions,
# gefiltert wird aber über Läufe — ein Ticket bringt es leicht auf ein Dutzend (Planung,
# Ausführung, Fortsetzungen, Review, Unteragenten). Der Faktor ist großzügig genug, dass
# `limit` Sessions praktisch immer voll werden, und deckelt die Abfrage trotzdem hart.
RUN_SCAN_FACTOR = 12
RUN_SCAN_MAX = 3000

# Ebenen der Baumschließung. Spiegelt `worker/runtime.MAX_DELEGATION_DEPTH = 2`; bewusst
# als eigene Konstante, statt `worker.runtime` zu importieren — das zöge die halbe
# Agentenlaufzeit (Provider-Adapter, MCP-Client) in einen Lese-Endpoint.
TREE_LEVELS = 2

SESSION_STATUS = ("all", "live", "recent")


# ── Kleinkram ───────────────────────────────────────────────────────────────

def _clamp(value: int, low: int, high: int) -> int:
    return max(low, min(high, value))


def _aware(value: dt.datetime | None) -> dt.datetime | None:
    """Naive Zeitstempel als UTC lesen. SQLite liefert sie ohne Zone; ohne diese Zeile
    verschöbe sich dieselbe Zeile je nach Datenbank um Stunden."""
    if value is None:
        return None
    return value.replace(tzinfo=dt.timezone.utc) if value.tzinfo is None else value


def _iso(value: dt.datetime | None) -> str | None:
    """ISO-8601 mit expliziter Zone. Ein nacktes `datetime` würde als Ortszeit gelesen —
    die Zeitleiste stünde dann je nach Browser um Stunden daneben."""
    aware = _aware(value)
    return None if aware is None else aware.astimezone(dt.timezone.utc).isoformat()


def _seq(step_id: int, slot: int) -> int:
    """`seq` einer Zeile — dieselbe Rechnung wie in `services/office`."""
    return int(step_id) * SEQ_SLOTS + slot


def _sid(kind: str, ref: int) -> str:
    return f"{kind}:{ref}"


def _not_found() -> HTTPException:
    """Eine einzige Formulierung für „gibt es nicht" und „gehört dir nicht". Zwei
    unterscheidbare Antworten wären ein Verzeichnis fremder Tickets."""
    return HTTPException(404, "Session nicht gefunden")


# ── Autorisierung ───────────────────────────────────────────────────────────

async def _visible_runs(db: AsyncSession, user: User):
    """SQL-Bedingung: welche Läufe darf dieser Nutzer überhaupt sehen?

    Admins ohne Filter. Sonst: Läufe der erlaubten Projekte plus die **eigenen**
    projektlosen Läufe (Assistent, Job) — die haben keinen Projektraum, über den sie
    sonst sichtbar würden.
    """
    if user.global_role == GlobalRole.admin:
        return true()
    allowed = await compute_acl(db, user)
    return or_(
        Run.project_id.in_(allowed),
        and_(Run.project_id.is_(None), Run.owner_id == user.id),
    )


async def _authorize(db: AsyncSession, user: User, *, project_id: int | None,
                     owner_id: int | None) -> None:
    """Darf der Nutzer diese Session lesen? Sonst 404.

    Die Werte stammen vom Wurzellauf bzw. vom Ticket, nicht aus dem Pfad. Ein projektloser
    Lauf gehört seinem Eigentümer (und dem Admin); ohne beides ist er für niemanden außer
    dem Admin lesbar — ein herrenloser Lauf ist kein öffentlicher Lauf.
    """
    if project_id is not None:
        project = await db.get(Project, project_id)
        if project is None:
            raise _not_found()
        try:
            access = await build_access(project, user, db)
        except HTTPException:
            raise _not_found() from None
        if not access.has_role(ProjectRole.viewer):
            raise _not_found()
        return
    if user.global_role == GlobalRole.admin:
        return
    if owner_id is None or owner_id != user.id:
        raise _not_found()


# ── Laufmenge einer Session ─────────────────────────────────────────────────

async def _load_session_runs(db: AsyncSession, kind: str, ref: int) -> tuple[list[Run], Issue | None]:
    """Alle Läufe einer Session, aufsteigend. Wirft 404, wenn die Adresse ins Leere zeigt.

    `issue:` braucht keine Baumschließung — Unterläufe erben die `issue_id`, eine Abfrage
    über `ix_runs_issue_started` hat den ganzen Baum. `run:` schließt in zwei Runden über
    `parent_run_id`; tiefer kann der Baum nicht werden (`TREE_LEVELS`).
    """
    if kind == "issue":
        issue = await db.get(Issue, ref)
        runs = (await db.execute(
            select(Run).where(Run.issue_id == ref).order_by(Run.id)
        )).scalars().all()
        # Ticket weg UND kein Lauf mehr: da war nie etwas oder es ist restlos gelöscht.
        # Ein noch vorhandenes Ticket ohne Läufe ist dagegen eine *aufgeräumte* Session
        # und bekommt eine ehrliche 200 mit `purged: true`.
        if issue is None and not runs:
            raise _not_found()
        return list(runs), issue

    root = await db.get(Run, ref)
    # Nur echte Wurzeln sind adressierbar: hätte ein Kindlauf seine eigene `run:`-Adresse,
    # gäbe es zwei Räume für denselben Baum und das Zurückspulen zeigte je nach Link
    # etwas anderes.
    if root is None or root.issue_id is not None or root.parent_run_id is not None:
        raise _not_found()
    runs = [root]
    frontier = [root.id]
    for _ in range(TREE_LEVELS):
        kids = (await db.execute(
            select(Run).where(Run.parent_run_id.in_(frontier)).order_by(Run.id)
        )).scalars().all()
        if not kids:
            break
        runs.extend(kids)
        frontier = [k.id for k in kids]
    runs.sort(key=lambda r: r.id)
    return runs, None


def _root_run(runs: list[Run]) -> Run | None:
    """Der Lauf, an dem die Session hängt: die Wurzel, sonst der älteste."""
    for run in runs:
        if run.parent_run_id is None:
            return run
    return runs[0] if runs else None


# ── Aggregate (je EINE Abfrage, nie eine je Lauf) ───────────────────────────

async def _step_bounds(db: AsyncSession, run_ids: list[int]) -> dict[int, dict]:
    """Je Lauf: erste/letzte Schritt-ID, Anzahl und ob eigene Grenzzeilen da sind.

    Die beiden `run_start`/`run_end`-Zähler entscheiden, ob `run_boundary_events` die
    Grenzen synthetisieren muss. Sie hier mitzurechnen kostet nichts und erspart eine
    zweite Runde — und vor allem: es rät nicht anhand der (womöglich gekappten) geladenen
    Schritte, ob ein `run_start` existiert.
    """
    if not run_ids:
        return {}
    rows = (await db.execute(
        select(
            RunStep.run_id, func.min(RunStep.id), func.max(RunStep.id), func.count(),
            func.sum(case((RunStep.kind == "run_start", 1), else_=0)),
            func.sum(case((RunStep.kind == "run_end", 1), else_=0)),
        ).where(RunStep.run_id.in_(run_ids)).group_by(RunStep.run_id)
    )).all()
    return {
        run_id: {"first": first, "last": last, "count": int(count or 0),
                 "has_start": bool(starts or 0), "has_end": bool(ends or 0)}
        for run_id, first, last, count, starts, ends in rows
    }


def _entry_priced(priced: bool | None, provider: str, model: str, prices: PriceTable) -> bool:
    """Ist dieser Kostenposten bepreist?

    Wo `priced` steht, gilt es — auch wenn der Katalogeintrag inzwischen gelöscht wurde.
    `api/cost.py:148` hält ausdrücklich fest, dass ein gelöschter Katalogeintrag alte
    Läufe nicht verändern darf. Nur die dreiwertige NULL (Altzeile, die die Unterscheidung
    nie kannte) wird zur Lesezeit gegen den Katalog aufgelöst.
    """
    if priced is not None:
        return bool(priced)
    return prices.has(provider or "", model or "")


async def _billed_by_run(db: AsyncSession, run_ids: list[int],
                         prices: PriceTable) -> dict[int, dict]:
    """Abgerechnete Kosten je Lauf aus `cost_entries` — der autoritative Betrag.

    Gruppiert bis auf (Provider, Modell, priced) herunter, weil `unpriced_models`
    benennen soll, WELCHES Modell keinen Preis hatte; eine reine Summe je Lauf könnte das
    nicht mehr sagen.
    """
    if not run_ids:
        return {}
    rows = (await db.execute(
        select(CostEntry.run_id, CostEntry.provider, CostEntry.model, CostEntry.priced,
               func.sum(CostEntry.input_tokens), func.sum(CostEntry.output_tokens),
               func.sum(CostEntry.cache_read_tokens), func.sum(CostEntry.cost_usd))
        .where(CostEntry.run_id.in_(run_ids))
        .group_by(CostEntry.run_id, CostEntry.provider, CostEntry.model, CostEntry.priced)
    )).all()
    out: dict[int, dict] = {}
    for run_id, provider, model, priced, in_tok, out_tok, cache_read, usd in rows:
        agg = out.setdefault(run_id, {
            "in_tokens": 0, "out_tokens": 0, "cache_read_tokens": 0,
            "cost_usd": 0.0, "priced": True, "unpriced_models": [],
        })
        agg["in_tokens"] += int(in_tok or 0)
        agg["out_tokens"] += int(out_tok or 0)
        agg["cache_read_tokens"] += int(cache_read or 0)
        agg["cost_usd"] += float(usd or 0.0)
        if not _entry_priced(priced, provider or "", model or "", prices):
            agg["priced"] = False
            label = f"{provider or ''}/{model or ''}"
            if label not in agg["unpriced_models"]:
                agg["unpriced_models"].append(label)
    return out


async def _step_tokens(db: AsyncSession, run_ids: list[int]) -> dict[tuple[int, str, str], dict]:
    """Tokens je (Lauf, Provider, Modell) **des Schritts**.

    Der Schritt weiß, wer tatsächlich geantwortet hat; `run.model` weiß nur, wer gefragt
    wurde. Ist der Lauf mitten drin auf den Fallback-Provider gewechselt, ist die
    Gruppierung nach `run.model` schlicht falsch — sie schriebe die Tokens des einen
    Modells dem anderen zu.
    """
    if not run_ids:
        return {}
    rows = (await db.execute(
        select(RunStep.run_id, RunStep.provider, RunStep.model,
               func.sum(RunStep.in_tokens), func.sum(RunStep.out_tokens),
               func.sum(RunStep.cache_read_tokens))
        .where(RunStep.run_id.in_(run_ids))
        .group_by(RunStep.run_id, RunStep.provider, RunStep.model)
    )).all()
    out: dict[tuple[int, str, str], dict] = {}
    for run_id, provider, model, in_tok, out_tok, cache_read in rows:
        tokens = (int(in_tok or 0), int(out_tok or 0), int(cache_read or 0))
        if not any(tokens):
            continue    # Zeilen ohne Tokens (Werkzeuge, Systemtext) sind keine Modellzüge
        key = (run_id, provider or "", model or "")
        agg = out.setdefault(key, {"in_tokens": 0, "out_tokens": 0, "cache_read_tokens": 0})
        agg["in_tokens"] += tokens[0]
        agg["out_tokens"] += tokens[1]
        agg["cache_read_tokens"] += tokens[2]
    return out


def _token_groups(run: Run, by_key: dict[tuple[int, str, str], dict]) -> list[tuple[str, str, dict]]:
    """Die Modellzüge eines Laufs — mit Rückfall auf die `runs`-Zeile.

    Ein Lauf von vor der Instrumentierung hat keine Tokens an den Schritten, wohl aber
    seine Summen am Lauf. Ohne diesen Rückfall wäre die Schätzung am ersten Tag überall 0
    und die ganze Kostenansicht nutzlos. Der Rückfall kennt naturgemäß nur EIN Modell —
    ein Fallback-Wechsel bleibt in Altdaten unsichtbar, weil ihn damals niemand notiert hat.
    """
    groups = [(provider, model, agg) for (rid, provider, model), agg in by_key.items()
              if rid == run.id]
    if groups:
        return groups
    if not (run.input_tokens or run.output_tokens):
        return []
    return [(run.provider or "", run.model or "",
             {"in_tokens": int(run.input_tokens or 0), "out_tokens": int(run.output_tokens or 0),
              "cache_read_tokens": 0})]


# ── Sessionliste ────────────────────────────────────────────────────────────

async def _resolve_sids(db: AsyncSession, runs: list[Run]) -> tuple[dict[int, str], list[Run]]:
    """run_id → `sid`, und die dafür nachgeladenen Elternläufe.

    Ein Kindlauf ohne Ticket muss beim WURZELLAUF landen, sonst bekäme ein delegierter
    Unteragent in der Liste ein eigenes Büro — und dieses Büro wäre über
    `/sessions/run/{id}` nicht einmal abrufbar (nur Wurzeln sind adressierbar).
    `session_id()` springt nur einen Schritt nach oben, hier wird die Kette zu Ende
    gegangen: fehlende Eltern werden je Runde in EINER Abfrage über den Primärschlüssel
    nachgeladen, höchstens `TREE_LEVELS` Runden.

    Die nachgeladenen Eltern kommen als Mitglieder in die Session (sonst stimmten
    `runs`, `started_at` und die Kosten nicht) — auch wenn sie außerhalb des Zeitfensters
    oder anders archiviert sind. Der Baum ist die Einheit, nicht das Fenster.
    """
    by_id = {r.id: r for r in runs}
    extra: list[Run] = []
    for _ in range(TREE_LEVELS):
        missing = {r.parent_run_id for r in by_id.values()
                   if r.issue_id is None and r.parent_run_id and r.parent_run_id not in by_id}
        if not missing:
            break
        parents = (await db.execute(select(Run).where(Run.id.in_(missing)))).scalars().all()
        if not parents:
            break
        extra.extend(parents)
        by_id.update({p.id: p for p in parents})
    sids: dict[int, str] = {}
    for run in by_id.values():
        node = run
        # Begrenzt, damit ein (durch Datenschaden) zyklischer parent_run_id nicht hängt.
        for _ in range(TREE_LEVELS + 1):
            parent = by_id.get(node.parent_run_id) if node.parent_run_id else None
            if parent is None:
                break
            node = parent
        # `session_id` bleibt die eine Wahrheit über die Adressform — inklusive ihres
        # eigenen Rückfalls, wenn der Elternlauf gar nicht mehr existiert.
        sids[run.id] = session_id(node)
    return sids, extra


async def _sessions_payload(db: AsyncSession, *, where, limit: int, since_hours: int,
                            status: str, archived: bool) -> dict:
    """Der gemeinsame Rumpf beider Sessionlisten — Projekt-Reiter und globale Seite zeigen
    dieselbe Form, weil sie durch dieselbe Funktion gehen."""
    limit = _clamp(limit, 1, SESSION_LIMIT_MAX)
    since_hours = _clamp(since_hours, 1, SINCE_HOURS_MAX)
    if status not in SESSION_STATUS:
        raise HTTPException(400, f"status muss eines von {', '.join(SESSION_STATUS)} sein")
    now = dt.datetime.now(dt.timezone.utc)
    cutoff = now - dt.timedelta(hours=since_hours)
    scan = min(limit * RUN_SCAN_FACTOR, RUN_SCAN_MAX)

    rows = (await db.execute(
        select(Run, Issue.key, Issue.summary, Issue.project_id)
        .outerjoin(Issue, Issue.id == Run.issue_id)
        .where(where, Run.started_at >= cutoff, Run.archived.is_(archived))
        .order_by(Run.id.desc()).limit(scan)
    )).all()
    runs = [r for r, _k, _s, _p in rows]
    issue_meta = {r.id: (key, summary, issue_pid) for r, key, summary, issue_pid in rows}
    if not runs:
        return {"live_window_ms": LIVE_WINDOW_MS, "sessions": []}

    sids, extra = await _resolve_sids(db, runs)
    runs.extend(extra)
    run_ids = [r.id for r in runs]

    # Projekt-Schlüssel in EINER Abfrage. Der Lauf trägt sein Projekt seit Welle A selbst;
    # bei Altzeilen ohne `project_id` steht es noch am Ticket.
    project_ids = {r.project_id for r in runs if r.project_id} | {
        pid for _k, _s, pid in issue_meta.values() if pid}
    project_keys: dict[int, str] = {}
    if project_ids:
        project_keys = dict((await db.execute(
            select(Project.id, Project.key).where(Project.id.in_(project_ids)))).all())

    bounds = await _step_bounds(db, run_ids)
    prices = await PriceTable.load(db)
    billed = await _billed_by_run(db, run_ids, prices)

    grouped: dict[str, list[Run]] = {}
    for run in runs:
        grouped.setdefault(sids.get(run.id) or session_id(run), []).append(run)

    sessions = []
    for sid, members in grouped.items():
        members.sort(key=lambda r: r.id)
        kind, _, ref = sid.partition(":")
        root = _root_run(members)
        key, summary, issue_pid = issue_meta.get(root.id, ("", "", None))
        if not key:
            # Der Wurzellauf kann außerhalb des Fensters nachgeladen worden sein — dann
            # steht die Ticket-Beschriftung an einem der Mitglieder.
            for member in members:
                key, summary, issue_pid = issue_meta.get(member.id, ("", "", None))
                if key:
                    break
        project_id = root.project_id or issue_pid
        events = sum(bounds.get(r.id, {}).get("count", 0) for r in members)
        starts = [_aware(r.started_at) for r in members if r.started_at]
        ends = [_aware(r.finished_at) for r in members if r.finished_at]
        last_event = max([*starts, *ends], default=None)
        running = any((r.status or "") == "running" for r in members)
        # Der DB-Status allein lügt nach einem Worker-Absturz: `status='running'` bleibt
        # dann für immer stehen. `api/runs.py:72` musste deshalb den Redis-Hash lesen.
        # Hier tut es das 90-s-Fenster — es kostet keine Runde und behauptet nichts.
        live = running and last_event is not None and (
            (now - last_event).total_seconds() * 1000 <= LIVE_WINDOW_MS)
        if status == "live" and not live:
            continue
        if status == "recent" and live:
            continue
        cost = [billed[r.id] for r in members if r.id in billed]
        sessions.append({
            "sid": sid, "kind": kind, "ref": int(ref) if ref.isdigit() else ref,
            "title": (summary or "").strip() or (root.agent or f"Lauf {root.id}"),
            "issue_key": key or "", "project_id": project_id,
            "project_key": project_keys.get(project_id or 0, ""),
            "started_at": _iso(min(starts) if starts else None),
            "last_event_at": _iso(last_event),
            "live": live,
            "runs": len(members),
            "agents": len({r.agent or "" for r in members}),
            "events": events,
            "archived": all(r.archived for r in members),
            # Kein einziger Schritt mehr: entweder von der Aufbewahrung gelöscht oder nie
            # geschrieben. Beides heißt für den Raum dasselbe — es gibt nichts abzuspielen.
            "purged": events == 0,
            "status": "running" if running else (max(members, key=lambda r: r.id).status or ""),
            # Tokens vom Lauf (die stehen dort immer), gecachte Tokens nur aus den
            # Kostenposten — der Lauf führt sie nicht getrennt.
            "in_tokens": sum(int(r.input_tokens or 0) for r in members),
            "out_tokens": sum(int(r.output_tokens or 0) for r in members),
            "cache_read_tokens": sum(c["cache_read_tokens"] for c in cost),
            "cost_usd": round(sum(c["cost_usd"] for c in cost), 6),
            "cost_partial": any(not c["priced"] for c in cost),
        })
    sessions.sort(key=lambda s: (s["last_event_at"] or "", s["sid"]), reverse=True)
    return {"live_window_ms": LIVE_WINDOW_MS, "sessions": sessions[:limit]}


@router.get("/projects/{project_id}/office/sessions")
async def project_sessions(
    access: Access = Depends(get_project_access),
    db: AsyncSession = Depends(get_session),
    limit: int = SESSION_LIMIT_DEFAULT,
    since_hours: int = SINCE_HOURS_DEFAULT,
    status: str = "all",
    archived: bool = False,
):
    """Die Sessions eines Projekts — der Inhalt des Projekt-Reiters „🏢 Büro".

    Fremdes Projekt = 404, das erledigt `get_project_access`.
    """
    pid = access.project.id
    # Bevorzugt über `Run.project_id` (überlebt die Ticket-Löschung und trägt auch
    # projektlose… nein: gerade die projektgebundenen Job-Läufe ohne Ticket). Alt-Zeilen
    # ohne `project_id` weiter über das Ticket zählen — genau wie `api/cost.py:28-31`.
    where = or_(Run.project_id == pid,
                and_(Run.project_id.is_(None), Issue.project_id == pid))
    return await _sessions_payload(db, where=where, limit=limit, since_hours=since_hours,
                                   status=status, archived=archived)


@router.get("/office/sessions")
async def global_sessions(
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_session),
    limit: int = SESSION_LIMIT_DEFAULT,
    since_hours: int = SINCE_HOURS_DEFAULT,
    status: str = "all",
    project_id: int | None = None,
):
    """Alle Sessions, die dieser Nutzer sehen darf — die Vollbildseite `/buero`.

    `project_id` **verengt** die ohnehin erlaubte Menge und autorisiert nie: ein fremdes
    Projekt einzutragen liefert eine leere Liste, keinen Zugang. Der Filter steht deshalb
    als zusätzliches UND neben der Sichtbarkeitsbedingung, nicht an ihrer Stelle.
    """
    where = await _visible_runs(db, user)
    if project_id is not None:
        where = and_(where, Run.project_id == project_id)
    return await _sessions_payload(db, where=where, limit=limit, since_hours=since_hours,
                                   status=status, archived=False)


# ── Ereignisse ──────────────────────────────────────────────────────────────

def _agent_row(run: Run, billed: dict | None) -> dict:
    """Eine Zeile des `agents[]`-Rosters — direkt aus `runs`, nicht aus den Ereignissen.

    Der Roster verdient seinen Platz genau dann, wenn gekappt wird: abgeschnitten wird vom
    ÄLTESTEN Ende (der Raum soll die Gegenwart zeigen), und damit fielen zuerst die
    `run_start`-Ereignisse weg — das Büro bliebe leer, obwohl alle Agenten da sind.
    """
    tokens = billed or {}
    return {
        "run_id": run.id, "agent_id": f"run:{run.id}", "agent": run.agent or "",
        "phase": run.phase or "", "provider": run.provider or "", "model": run.model or "",
        "parent_run_id": run.parent_run_id, "parent_tool_use_id": run.parent_tool_use_id,
        "spawn_depth": int(run.spawn_depth or 0), "status": run.status or "",
        "blocker_kind": run.blocker_kind,
        "started_at": _iso(run.started_at), "finished_at": _iso(run.finished_at),
        "continuation_index": int(run.continuation_index or 0),
        "in_tokens": tokens.get("in_tokens", int(run.input_tokens or 0)),
        "out_tokens": tokens.get("out_tokens", int(run.output_tokens or 0)),
        "cache_read_tokens": tokens.get("cache_read_tokens", 0),
        "cost_usd": round(tokens.get("cost_usd", float(run.cost_usd or 0.0)), 6),
        # Dreiwertig: ohne Kostenposten ist nichts abgerechnet — das ist nicht „unbepreist",
        # sondern „noch nicht bekannt". Ein `False` hier hätte jeden laufenden Lauf als
        # Preislücke gemeldet.
        "cost_priced": None if billed is None else billed["priced"],
    }


@router.get("/office/sessions/{kind}/{ref}/events")
async def session_events(
    kind: str, ref: int,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_session),
    limit: int = EVENT_CAP_DEFAULT,
    after_seq: int = 0,
):
    """Der Schnappschuss eines Raums: Roster plus Ereignisse, streng nach `seq` aufsteigend.

    `seq` ist die Ankunftsreihenfolge (`run_steps.id`), **nie** `ts` — unter
    `WORKER_CONCURRENCY > 1` kann der Zeitstempel gegenüber der Reihenfolge rückwärts
    laufen, und nach `ts` sortiert spielte der Raum dann Ereignisse ab, die es so nie gab.

    Bei Kappung fällt das ÄLTESTE weg; `seq_from` sagt dem Client, wo sein Log anfängt.
    """
    if kind not in ("issue", "run"):
        raise _not_found()
    cap = _clamp(limit, 1, EVENT_CAP_MAX)
    runs, issue = await _load_session_runs(db, kind, ref)
    sid = _sid(kind, ref)

    root = _root_run(runs)
    await _authorize(db, user,
                     project_id=(root.project_id if root else None) or (issue.project_id if issue else None),
                     owner_id=root.owner_id if root else None)
    if not runs:
        # Aufgeräumt: das Ticket steht noch, seine Läufe sind der Aufbewahrung zum Opfer
        # gefallen. Eine 404 wäre hier eine Lüge — die Session gab es, und die UI soll es
        # sagen dürfen („dieser Raum wurde geräumt") statt „nicht gefunden".
        return {"sid": sid, "v": EVENT_VERSION, "seq_from": 0, "seq_to": 0, "count": 0,
                "truncated": False, "purged": True, "agents": [], "events": []}

    run_ids = [r.id for r in runs]
    bounds = await _step_bounds(db, run_ids)
    total_steps = sum(b["count"] for b in bounds.values())

    # Ein Schritt trägt `seq = id * SEQ_SLOTS + slot`; die untere Schranke ist also
    # `after_seq // SEQ_SLOTS`. Feiner filtert danach Python auf dem fertigen Ereignis —
    # eine Zeile kann bis zu vier `seq` tragen, die SQL-Grenze kann das nicht auflösen.
    after_id = max(0, after_seq // SEQ_SLOTS)
    # Absteigend holen und danach umdrehen: gekappt wird vom ältesten Ende. Aufsteigend
    # mit LIMIT bekäme man den ANFANG des Logs — also ausgerechnet das, was niemand sehen
    # will, wenn gerade etwas läuft. `cap + 1` verrät die Kappung ohne ein zweites COUNT.
    step_rows = (await db.execute(
        select(RunStep).where(RunStep.run_id.in_(run_ids), RunStep.id >= after_id)
        .order_by(RunStep.id.desc()).limit(cap + 1)
    )).scalars().all()
    truncated = len(step_rows) > cap
    steps = sorted(step_rows[:cap] if truncated else list(step_rows), key=lambda s: s.id)

    issue_keys: dict[int, str] = {}
    issue_ids = {r.issue_id for r in runs if r.issue_id}
    if issue_ids:
        issue_keys = dict((await db.execute(
            select(Issue.id, Issue.key).where(Issue.id.in_(issue_ids)))).all())

    ctxs = {r.id: RunCtx.from_run(r, issue_key=issue_keys.get(r.issue_id or 0, "")) for r in runs}
    events: list[dict] = []
    for step in steps:
        ctx = ctxs.get(step.run_id)
        if ctx is not None:
            events.extend(step_events(step, ctx))
    # Grenzen nur für Läufe, die keine eigenen haben (Altläufe). Welle B schreibt sie
    # selbst; die hier zusätzlich zu synthetisieren gäbe jeden Agenten doppelt.
    for run in runs:
        b = bounds.get(run.id)
        if b is None:
            continue
        events.extend(run_boundary_events(
            run, ctxs[run.id],
            first_step_id=None if b["has_start"] else b["first"],
            last_step_id=None if b["has_end"] else b["last"],
        ))

    events = [e for e in events if e["seq"] > after_seq]
    if truncated and steps:
        # Was unterhalb der Kappung liegt, fliegt auch dann raus, wenn es eine
        # synthetisierte Grenze ist — sonst stünde ein `run_start` unter `seq_from` und
        # der Client hielte sein Log für vollständig. Wer dort fehlt, steht im Roster.
        floor = _seq(steps[0].id, 0) - 1
        events = [e for e in events if e["seq"] >= floor]
    events.sort(key=lambda e: e["seq"])

    if events and after_seq <= 0:
        # Die Kopfzeile des Raums: Titel, Ticket, Projekt. Sie steht ganz vorn, direkt
        # unter dem ersten echten Ereignis. Nur beim Vollabruf — beim Nachfassen mit
        # `after_seq` hat der Client sie längst und bekäme sie sonst mit neuer `seq`
        # ein zweites Mal (die Entdopplung des Recorders läuft über `seq`).
        project_key = ""
        if root.project_id or (issue and issue.project_id):
            project = await db.get(Project, root.project_id or issue.project_id)
            project_key = project.key if project else ""
        title = (issue.summary if issue else "") or root.agent or f"Lauf {root.id}"
        events.insert(0, session_seen_event(
            ctxs[root.id], title=title, project_key=project_key,
            started_at=root.started_at, seq=events[0]["seq"] - 1))

    billed = await _billed_by_run(db, run_ids, await PriceTable.load(db))
    return {
        "sid": sid, "v": EVENT_VERSION,
        "seq_from": events[0]["seq"] if events else 0,
        "seq_to": events[-1]["seq"] if events else 0,
        "count": len(events), "truncated": truncated,
        "purged": total_steps == 0,
        "agents": [_agent_row(r, billed.get(r.id)) for r in runs],
        "events": events,
    }


# ── Kosten ──────────────────────────────────────────────────────────────────

@router.get("/office/sessions/{kind}/{ref}/cost")
async def session_cost(
    kind: str, ref: int,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_session),
):
    """Zwei Zahlen mit Absicht — abgerechnet und geschätzt.

    `cost_usd_billed` ist die Summe der `cost_entries`: was zum Zeitpunkt des Laufs
    tatsächlich in Rechnung ging. `cost_usd_estimated` rechnet die **Schritt**-Tokens
    gegen den HEUTIGEN Katalog, gruppiert nach dem Modell des Schritts — das bleibt auch
    dann richtig, wenn der Lauf mitten drin auf den Fallback-Provider gewechselt ist.
    Beide stehen nebeneinander, keine überschreibt die andere: die eine sagt, was es
    gekostet hat, die andere, was es heute kosten würde.

    `unpriced` unterscheidet, was Traccoon bisher nicht unterscheiden konnte: ein
    Katalogeintrag mit Preis 0,00 ist *bepreist und gratis* (das lokale Modell), gar kein
    Eintrag ist *unbekannt*. Beides ergab bisher dieselbe 0,00, und jede Katalog-Lücke sah
    aus wie ein Geschenk.
    """
    if kind not in ("issue", "run"):
        raise _not_found()
    runs, issue = await _load_session_runs(db, kind, ref)
    root = _root_run(runs)
    await _authorize(db, user,
                     project_id=(root.project_id if root else None) or (issue.project_id if issue else None),
                     owner_id=root.owner_id if root else None)

    prices = await PriceTable.load(db)
    run_ids = [r.id for r in runs]
    billed = await _billed_by_run(db, run_ids, prices)
    steps = await _step_tokens(db, run_ids)

    total = {"in_tokens": 0, "out_tokens": 0, "cache_read_tokens": 0,
             "cost_usd_billed": 0.0, "cost_usd_estimated": 0.0}
    by_agent: dict[str, dict] = {}
    by_model: dict[tuple[str, str], dict] = {}

    for run in runs:
        agent = run.agent or ""
        row = by_agent.setdefault(agent, {
            "agent": agent, "run_ids": [], "runs": 0,
            "in_tokens": 0, "out_tokens": 0, "cache_read_tokens": 0,
            "cost_usd_billed": 0.0, "cost_usd_estimated": 0.0,
            "unpriced": False, "unpriced_models": [],
        })
        row["run_ids"].append(run.id)
        row["runs"] += 1

        entry = billed.get(run.id)
        if entry is not None:
            row["cost_usd_billed"] += entry["cost_usd"]
            total["cost_usd_billed"] += entry["cost_usd"]
            if not entry["priced"]:
                # Die Preislücke steht auf der ABRECHNUNGS-Seite: dieser Betrag ist ohne
                # Katalogeintrag entstanden und damit nicht belastbar. Deshalb zeigt die
                # UI hier ein „≥" statt einer Summe, die Genauigkeit vortäuscht.
                row["unpriced"] = True
                for label in entry["unpriced_models"]:
                    if label not in row["unpriced_models"]:
                        row["unpriced_models"].append(label)

        for provider, model, tokens in _token_groups(run, steps):
            cost, priced = prices.price(
                provider, model, in_tokens=tokens["in_tokens"], out_tokens=tokens["out_tokens"],
                cache_read_tokens=tokens["cache_read_tokens"])
            for field in ("in_tokens", "out_tokens", "cache_read_tokens"):
                row[field] += tokens[field]
                total[field] += tokens[field]
            row["cost_usd_estimated"] += cost
            total["cost_usd_estimated"] += cost
            model_row = by_model.setdefault((provider, model), {
                "provider": provider, "model": model, "in_tokens": 0, "out_tokens": 0,
                "cache_read_tokens": 0, "cost_usd": 0.0, "unpriced": not priced,
            })
            for field in ("in_tokens", "out_tokens", "cache_read_tokens"):
                model_row[field] += tokens[field]
            model_row["cost_usd"] += cost

    for row in (*by_agent.values(), total):
        for field in ("cost_usd_billed", "cost_usd_estimated"):
            row[field] = round(row[field], 6)
    for model_row in by_model.values():
        model_row["cost_usd"] = round(model_row["cost_usd"], 6)

    return {
        # Genau die Aussage „mindestens": irgendein abgerechneter Posten hatte keinen
        # Preis hinter sich. Ein noch laufender Lauf ohne Kostenposten ist NICHT partial —
        # der hat noch nicht abgerechnet, das ist kein Fehler.
        "cost_partial": any(row["unpriced"] for row in by_agent.values()),
        "total": total,
        "by_agent": sorted(by_agent.values(), key=lambda r: r["agent"]),
        "by_model": sorted(by_model.values(), key=lambda r: (r["provider"], r["model"])),
    }
