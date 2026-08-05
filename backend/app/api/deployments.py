"""Deployments — die Lese-API. Drei Routen auf eine Tabelle, die 186 Zeilen lang
geschrieben und nie gelesen wurde.

Muster durchgehend `api/office.py`: nackte Dicts statt Pydantic-Schemata, **404 statt
403**, und die Berechtigung kommt aus der **Zeile** (ihrem `project_id`), nicht aus dem
Pfad — ein Pfad, der die Projekt-ID trägt, überließe dem Client die Autorisierung.

Warum es eine **globale** Route gibt und nicht nur die projektbezogene: die 17
Wartungs-Updates (`self_deploy`) hängen an keinem Ticket, und ein Deployment ohne Projekt
(`project_id IS NULL`, heute keins, ab der ersten `SET NULL`-Projektlöschung eins) wäre
sonst für niemanden auffindbar.

**Vier Eigenheiten des Bestands prägen die Antwortform** — jede davon ist nachgemessen,
keine geraten:

1. `requested_by` und `chat_id` sind bei **0 von 186** Zeilen gefüllt; keine der vier
   Schreibstellen setzt sie. „Wer hat das ausgelöst" ist mit dem heutigen Schema nicht
   darstellbar. Dafür gibt es die neue Spalte `source` — und weil sie noch niemand füllt,
   bildet die API `"" → "unbekannt"` ab, statt aus `self_deploy` eine Herkunft zu raten.
   Die beiden Altspalten tauchen in **keiner** Antwort auf.
2. Alle 56 `failed` tragen **denselben** Wächter-Text („Abgelehnt: Self-Deploy nur über
   das explizite Wartungs-…"). Eine Liste ohne `log_head` wäre deshalb nicht bloß dünn,
   sondern aktiv irreführend — sie zeigte 56 verschiedene Fehlschläge, wo einer steht.
   Der Volltext bleibt dem Detail-Endpunkt vorbehalten (siehe `deployment_detail`).
3. 69 Zeilen stehen auf `cancelled`, einem Status, den **kein Codepfad schreibt**
   (Herleitung im Docstring von `models/ops.Deployment`). Sie werden gezeigt, aber nicht
   kanonisiert: ihre `phase` ist `aborted` und ihr `ok` ist `None`.
4. **71 von 186 Zeilen fehlt ein Zeitstempel.** `wait_ms`/`duration_ms` sind deshalb
   dreiwertig — eine gerechnete 0 wäre eine erfundene Dauer.

`ok` folgt derselben Regel wie `services/office.tool_ok`: **nie ein geratenes Ergebnis**.
Belegter Erfolg `True`, belegter Fehlschlag `False`, alles andere `None`.
"""
from __future__ import annotations

import datetime as dt

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import and_, func, select, true
from sqlalchemy.ext.asyncio import AsyncSession

from ..db import get_session
from ..models.enums import GlobalRole, ProjectRole
from ..models.ops import Deployment
from ..models.project import Project
from ..models.ticket import Issue
from ..models.user import User
from .deps import Access, build_access, get_current_user, get_project_access
# Dieselbe Definition von „darf sehen" wie Büro und Projektliste. Es soll genau eine
# geben, nicht drei, die irgendwann auseinanderlaufen.
from .office_ws import compute_acl

router = APIRouter(tags=["deployments"])

LIMIT_DEFAULT = 50
LIMIT_MAX = 200

# Deutlich länger als das Büro (dort eine Woche): ein Deployment ist **Archiv**, keine
# Sitzung. Die Frage „wann lief das zuletzt durch" ist Monate später noch dieselbe Frage,
# während ein Agentenlauf nach 30 Tagen ohnehin der Aufbewahrung zum Opfer fällt.
SINCE_HOURS_DEFAULT = 24 * 30
SINCE_HOURS_MAX = 24 * 365

LOG_HEAD_CHARS = 240

# Die Statusmengen, aus denen `phase`, `ok` und der Filter entstehen. `status` selbst geht
# **roh** durch die Antwort — die Ansicht soll `pending-check` von `pending` unterscheiden
# können, auch wenn beide dieselbe Phase haben.
OPEN_STATUS = ("pending", "pending-check", "building")
FAILED_STATUS = ("failed", "rolledback")

STATUS_FILTER = ("all", "running", "ok", "failed", "other")


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
    """ISO-8601 mit expliziter Zone. Ein nacktes `datetime` würde im Browser als Ortszeit
    gelesen — dieselbe Zeile stünde je nach Besucher um Stunden daneben."""
    aware = _aware(value)
    return None if aware is None else aware.astimezone(dt.timezone.utc).isoformat()


def _span_ms(start: dt.datetime | None, end: dt.datetime | None) -> int | None:
    """Millisekunden zwischen zwei Zeitstempeln — oder `None`, wenn einer fehlt.

    Der Rückfall auf 0 wäre bequem und falsch: 71 der 186 Bestandszeilen haben kein
    `finished_at`, 58 kein `started_at`. Eine 0 dort behauptete einen Deploy, der keine
    Zeit gebraucht hat, statt einen, dessen Zeit niemand aufgeschrieben hat.
    """
    a, b = _aware(start), _aware(end)
    if a is None or b is None:
        return None
    return int((b - a).total_seconds() * 1000)


def _phase(status: str) -> str:
    """`queued|running|done|aborted` — die grobe Lage, für Farbe und Sortierung.

    Unbekanntes landet bei `aborted` und nicht bei `done`: ein Status, den diese Datei
    nicht kennt, ist kein abgeschlossener Deploy, sondern einer, über den nichts bekannt
    ist. `cancelled` fällt genau deshalb hierher — es ist der einzige heute vorkommende
    Vertreter (siehe Modul-Docstring, Punkt 3).
    """
    if status in ("pending", "pending-check"):
        return "queued"
    if status == "building":
        return "running"
    if status == "ok" or status in FAILED_STATUS:
        return "done"
    return "aborted"


def _ok(status: str) -> bool | None:
    """Dreiwertig, nach der Hausregel aus `services/office.tool_ok`: **nie ein geratenes
    Ergebnis**. `True` nur bei belegtem Erfolg, `False` nur bei belegtem Fehlschlag,
    sonst `None` — offen (läuft noch) und abgebrochen (weiß niemand) sind beide „unbekannt",
    aber aus verschiedenen Gründen; die `phase` trennt sie.
    """
    if status == "ok":
        return True
    if status in FAILED_STATUS:
        return False
    return None


def _kind(self_deploy: bool, check_only: bool) -> str:
    """`self|check|stack`. Reihenfolge ist tragend: ein Self-Deploy ist nie ein bloßer
    Check, und `check_only` allein sagt nichts darüber, wessen Stack gemeint ist."""
    if self_deploy:
        return "self"
    if check_only:
        return "check"
    return "stack"


def _not_found() -> HTTPException:
    """Eine einzige Formulierung für „gibt es nicht" und „gehört dir nicht". Zwei
    unterscheidbare Antworten wären ein Verzeichnis fremder Projekte."""
    return HTTPException(404, "Deployment nicht gefunden")


# ── Autorisierung ───────────────────────────────────────────────────────────

async def _visible_deployments(db: AsyncSession, user: User):
    """SQL-Bedingung: welche Deployments darf dieser Nutzer überhaupt sehen?

    Vorbild ist `api/office.py::_visible_runs` — Admin ohne Filter, sonst die erlaubten
    Projekte. Ein Unterschied ist beabsichtigt: **`project_id IS NULL` bleibt Admins
    vorbehalten.** Der Lauf hat ein `owner_id`, an dem ein projektloser Lauf seinem
    Menschen zugeordnet werden kann; das Deployment hat **kein** solches Feld
    (`requested_by` ist bei 0 von 186 Zeilen gefüllt). Ein herrenloses Deployment gehört
    deshalb niemandem — und `IN (…)` liefert für NULL ohnehin NULL, also kein Treffer.
    """
    if user.global_role == GlobalRole.admin:
        return true()
    allowed = await compute_acl(db, user)
    return Deployment.project_id.in_(allowed)


async def _authorize_row(db: AsyncSession, user: User, project_id: int | None) -> None:
    """Darf der Nutzer diese eine Zeile lesen? Sonst 404 — in **beiden** Fällen.

    Die Projekt-ID stammt aus der geladenen Zeile, nicht aus dem Pfad; `/deployments/{id}`
    trägt gar keine. Dass „fremdes Projekt" und „gibt es nicht" ununterscheidbar antworten,
    ist der Punkt: sonst wäre die Route ein Zähler fremder Deployments.
    """
    if project_id is None:
        # Kein Projekt = kein Eigentümer, an dem man Sichtbarkeit festmachen könnte.
        if user.global_role != GlobalRole.admin:
            raise _not_found()
        return
    project = await db.get(Project, project_id)
    if project is None:
        raise _not_found()
    try:
        access = await build_access(project, user, db)
    except HTTPException:
        raise _not_found() from None
    if not access.has_role(ProjectRole.viewer):
        raise _not_found()


# ── Zeilenform + Abfrage ────────────────────────────────────────────────────

def _status_condition(status: str):
    """Der Filter über `?status=`. `all` gibt `None` zurück (kein UND).

    `running` meint „noch nicht entschieden" und umfasst deshalb die Warteschlange mit:
    wer wissen will, ob gerade etwas unterwegs ist, interessiert sich nicht dafür, ob der
    Sidecar die Zeile schon aufgegriffen hat. `other` ist der Rest — heute genau die 69
    `cancelled`, morgen jeder Status, den diese Datei noch nicht kennt.
    """
    if status == "all":
        return None
    if status == "running":
        return Deployment.status.in_(OPEN_STATUS)
    if status == "ok":
        return Deployment.status == "ok"
    if status == "failed":
        return Deployment.status.in_(FAILED_STATUS)
    return Deployment.status.notin_((*OPEN_STATUS, "ok", *FAILED_STATUS))


def _row(rec) -> dict:
    """Eine Zeile der Liste. `status` steht **roh** darin, nichts wird geschönt;
    `phase`/`ok` sind Ableitungen daneben, nicht an seiner Stelle."""
    (dep_id, project_id, project_key, issue_id, issue_key, status, source, self_deploy,
     check_only, stack_dir, created_at, started_at, finished_at, log_len, log_head) = rec
    status = status or ""
    return {
        "id": dep_id,
        "project_id": project_id,
        "project_key": project_key or "",
        "issue_id": issue_id,
        "issue_key": issue_key or "",
        "status": status,
        "phase": _phase(status),
        "ok": _ok(status),
        # Leer heißt „steht nicht in der Zeile", nicht „war niemand". Der Backfill wäre
        # heute richtig (`self_deploy → maintenance`) und würde falsch, sobald `merge`
        # oder `workflow` das erste Mal feuern — eine geratene Herkunft in einer
        # Historienansicht ist schlimmer als ein ehrliches Leerfeld.
        "source": source or "unbekannt",
        "kind": _kind(bool(self_deploy), bool(check_only)),
        "stack_dir": stack_dir or "",
        "created_at": _iso(created_at),
        "started_at": _iso(started_at),
        "finished_at": _iso(finished_at),
        # Zwei getrennte Zeiten, weil sie zwei getrennte Probleme benennen: `wait_ms` ist
        # die Warteschlange (der Sidecar pollt alle 3 s), `duration_ms` die eigentliche
        # Arbeit. Zusammengezählt sähe ein 3-s-Wartefall aus wie ein langsamer Build.
        "wait_ms": _span_ms(created_at, started_at),
        "duration_ms": _span_ms(started_at, finished_at),
        "log_bytes": int(log_len or 0),
        "log_head": log_head or "",
    }


def _select_rows():
    """Die Spaltenliste der Liste — **ohne** den Log-Volltext.

    `octet_length`/`substr` rechnen in der Datenbank, damit ein Abruf über 200 Zeilen
    nicht 200 vollständige Build-Logs über die Leitung zieht. Beide Funktionen gibt es
    unter Postgres wie unter SQLite (ab 3.43; das Backend-Image bringt 3.46).
    """
    return (
        select(
            Deployment.id, Deployment.project_id, Project.key,
            Deployment.issue_id, Issue.key,
            Deployment.status, Deployment.source,
            Deployment.self_deploy, Deployment.check_only, Deployment.stack_dir,
            Deployment.created_at, Deployment.started_at, Deployment.finished_at,
            func.octet_length(Deployment.log),
            func.substr(Deployment.log, 1, LOG_HEAD_CHARS),
        )
        .outerjoin(Project, Project.id == Deployment.project_id)
        .outerjoin(Issue, Issue.id == Deployment.issue_id)
    )


async def _payload(db: AsyncSession, *, where, limit: int, since_hours: int,
                   status: str) -> dict:
    """Der gemeinsame Rumpf beider Listen — Projektkarte und globale Seite zeigen dieselbe
    Form, weil sie durch dieselbe Funktion gehen.

    `where` trägt die **Sichtbarkeit plus alle Verengungen außer dem Status**. Das ist
    Absicht: `by_status` wird gegen genau dieses `where` gezählt und beantwortet damit die
    Frage „was liegt in diesem Fenster überhaupt herum", während die Liste bereits
    gefiltert ist. Andersherum wäre die Zählung eine Tautologie (`{"ok": 50}` bei
    `?status=ok`) und die 69 abgebrochenen blieben unerklärt — sie *in* die Liste zu
    mischen, würde sie dagegen vergiften.
    """
    if status not in STATUS_FILTER:
        raise HTTPException(400, f"status muss eines von {', '.join(STATUS_FILTER)} sein")
    limit = _clamp(limit, 1, LIMIT_MAX)
    since_hours = _clamp(since_hours, 1, SINCE_HOURS_MAX)
    cutoff = dt.datetime.now(dt.timezone.utc) - dt.timedelta(hours=since_hours)
    # `created_at` und nicht `finished_at`: eine Zeile ohne Ende (69 von 186) fiele sonst
    # aus jedem Fenster heraus und wäre über keine Route mehr erreichbar.
    window = and_(where, Deployment.created_at >= cutoff)

    cond = _status_condition(status)
    listed = window if cond is None else and_(window, cond)
    # `limit + 1` verrät die Kappung ohne ein zweites COUNT.
    rows = (await db.execute(
        _select_rows().where(listed).order_by(Deployment.id.desc()).limit(limit + 1)
    )).all()
    truncated = len(rows) > limit
    items = [_row(r) for r in rows[:limit]]

    counts = (await db.execute(
        select(Deployment.status, func.count()).where(window).group_by(Deployment.status)
    )).all()
    by_status = {(s or ""): int(n or 0)
                 for s, n in sorted(counts, key=lambda r: (-int(r[1] or 0), r[0] or ""))}
    return {"items": items, "count": len(items), "truncated": truncated,
            "by_status": by_status}


# ── Routen ──────────────────────────────────────────────────────────────────

@router.get("/projects/{project_id}/deployments")
async def project_deployments(
    access: Access = Depends(get_project_access),
    db: AsyncSession = Depends(get_session),
    limit: int = LIMIT_DEFAULT,
    since_hours: int = SINCE_HOURS_DEFAULT,
    status: str = "all",
    issue_id: int | None = None,
):
    """Die Deployments eines Projekts — Karte im Dashboard, volle Liste in den
    Einstellungen.

    Fremdes Projekt = 404, das erledigt `get_project_access`. **Viewer genügt**: wer
    gemergt hat, will wissen, ob es draußen ist, und ist dafür nicht zwangsläufig
    `maintainer`.
    """
    where = Deployment.project_id == access.project.id
    if issue_id is not None:
        where = and_(where, Deployment.issue_id == issue_id)
    return await _payload(db, where=where, limit=limit, since_hours=since_hours,
                          status=status)


@router.get("/deployments")
async def global_deployments(
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_session),
    limit: int = LIMIT_DEFAULT,
    since_hours: int = SINCE_HOURS_DEFAULT,
    status: str = "all",
    project_id: int | None = None,
):
    """Alle Deployments, die dieser Nutzer sehen darf.

    `project_id` **verengt** die ohnehin erlaubte Menge und autorisiert nie: ein fremdes
    Projekt einzutragen liefert eine leere Liste, keinen Zugang und keine 403 (die wäre
    ein Existenzbeweis). Der Filter steht deshalb als zusätzliches UND neben der
    Sichtbarkeitsbedingung, nicht an ihrer Stelle.
    """
    where = await _visible_deployments(db, user)
    if project_id is not None:
        where = and_(where, Deployment.project_id == project_id)
    return await _payload(db, where=where, limit=limit, since_hours=since_hours,
                          status=status)


@router.get("/deployments/{dep_id}")
async def deployment_detail(
    dep_id: int,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_session),
):
    """Eine Zeile mit dem **vollständigen Log** — der einzige Endpunkt, der ihn liefert.

    Der Volltext gehört genau hierher: in der Liste wären 56 identische Wächter-Meldungen
    Lärm, und ein `ok`-Log ist heute rund 1 kB — bei 200 Zeilen wäre die Liste ohne Not
    zwanzigmal so groß. Wer den Grund sehen will, klickt die Zeile an; `log_head`
    entscheidet, ob sich das lohnt.
    """
    dep = await db.get(Deployment, dep_id)
    if dep is None:
        raise _not_found()
    await _authorize_row(db, user, dep.project_id)

    project_key = ""
    if dep.project_id:
        project = await db.get(Project, dep.project_id)
        project_key = project.key if project else ""
    issue_key = ""
    if dep.issue_id:
        issue = await db.get(Issue, dep.issue_id)
        issue_key = issue.key if issue else ""

    log = dep.log or ""
    row = _row((
        dep.id, dep.project_id, project_key, dep.issue_id, issue_key, dep.status,
        dep.source, dep.self_deploy, dep.check_only, dep.stack_dir,
        dep.created_at, dep.started_at, dep.finished_at,
        len(log.encode("utf-8")), log[:LOG_HEAD_CHARS],
    ))
    row["log"] = log
    return row
