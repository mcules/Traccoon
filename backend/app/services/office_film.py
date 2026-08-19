"""End of day film: the whole office day as a time lapse GIF.

**One film for the day, not one per session.** The data decides that: a day has 15 to 44 runs
in as many sessions, most with a single run and around twenty steps. A film per session would
be twenty films of three frames each. The room with twelve seats, into which agents come and
from which they leave, **is** the day, and that is exactly the story a time lapse can carry.
exactly the story a time lapse can carry.

**Below the HTTP layer.** The events come from `services/office.step_events` and
`run_boundary_events`, the same functions `/office/sessions/…/events` goes through. No new
endpoint, no second reading of the `run_steps` rows: what the film shows is bit for bit what
the office shows.

**Three window traps**, all three real (the 36.5 hour session of run 404 exists):

1. A run that began **before** the window has its `run_start` row outside it.
   `run_boundary_events` adds it with `run.started_at`, so with yesterday's timestamp. That is
   **clamped** to the start of the window, otherwise the HUD clock in the opening shows the
   previous day and the error looks like a bug in the engine.
2. A run that ends **after** the window would get its `run_end` with tomorrow's timestamp. That
   is filtered **after** the events are built, not before: beforehand we would not know whether
   a boundary appears at all (a running run gets none).
3. `session_seen` is **not** produced here. The read API sets one header per room; twenty
   headers in one film would be twenty titles for one day. The film carries chapter cards
   instead, which the renderer cuts from the islands of activity.

**The exit is the notifier.** The backend container lacks `TELEGRAM_BOT_TOKEN` entirely, only
`telegram-bot` speaks to Telegram. The job therefore writes a `Notification` with
`media_path`/`media_kind`, and the bot sends it. A second exit would be a second truth about
who got what and when.

**Determinism:** `grade` comes from the job arguments, never from the clock. **Every** string
is built by Python and sent along ready made (including the weekday, see `WOCHENTAGE`), so no
ICU version in the renderer can change the image.
"""
from __future__ import annotations

import datetime as dt
import logging
import os
from dataclasses import dataclass

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..models.agents import CostEntry, Run, RunStep
from ..models.notification import Notification
from ..models.ticket import Issue
from .office import (
    EVENT_CAP_MAX, FAIL_STATUS, PriceTable, RunCtx, entdoppeln_seq, run_boundary_events,
    step_events,
)

log = logging.getLogger("office_film")

# The renderer is a sidecar without credentials: the backend sends the log along, the filmer
# fetches nothing. So a bare service name without auth is enough.
FILMER_URL = os.getenv("FILMER_URL", "http://filmer:8710")
# Where the finished films lie. Has to be mounted in the backend AND in the chat bot: one
# writes, the other sends.
FILM_DIR = os.getenv("FILM_DIR", "/data/film")

# Defaults for when `job.args` says nothing.
STD_TZ = "Europe/Berlin"
STD_SEKUNDEN = 25
STD_FPS = 12
STD_GRADE = "night"
STD_KAPITEL = 8
STD_BEHALTEN_TAGE = 14

# The same upper bound the renderer runs as `REPLAY_CAP`. The strongest real day had about
# 2500 events; an outlier day above that would otherwise lose the morning **silently**.
# Truncation is at the oldest end (as in `api/office.py`), and the caption says so.
EREIGNIS_CAP = EVENT_CAP_MAX

# A run waiting for a person. Not to be merged with "failure": a question is not a failure but
# an open question.
RUECKFRAGE_STATUS = ("blocked",)

# The weekdays by hand, not through `%a`: `strftime` depends on the locale, and in the container
# the locale is C. "Wed" would then stand under a German film.
WOCHENTAGE = ("Mo", "Di", "Mi", "Do", "Fr", "Sa", "So")

# Telegram cuts a caption at 1024 characters, so it is better to shorten it here than to be cut
# off mid word.
UNTERTITEL_MAX = 1024
TITEL_MAX = 60

# Response headers of the renderer. Looked up case insensitively (`_kopf`): httpx returns the
# names in lower case, the renderer writes them capitalised.
KOPF_KAPITEL = "X-Film-Kapitel"
KOPF_INSELN = "X-Film-Inseln"
KOPF_BILDER = "X-Film-Bilder"
KOPF_GEKAPPT = "X-Film-Gekappt"
# **Build time**, not playing time (`film.mjs`: `Date.now() - t0`). How long the film runs stands
# nowhere: that is `frames / fps` and is computed here.
KOPF_DAUER = "X-Film-Dauer-Ms"

# How much room the HTTP call leaves under `job.run_timeout`. The job has to be able to write
# the error itself; if the scheduler timeout beats it, the JobRun would stay on
# „running" stehen.
TIMEOUT_PUFFER_S = 30


def _now() -> dt.datetime:
    return dt.datetime.now(tz=dt.timezone.utc)


def _utc(value: dt.datetime | None) -> dt.datetime | None:
    """Read naive timestamps as UTC. SQLite delivers them without a zone; without it a comparison
    of two rows throws a TypeError or is off by hours, depending on the database."""
    if value is None:
        return None
    return value.replace(tzinfo=dt.timezone.utc) if value.tzinfo is None else value


def _iso_ms(value: dt.datetime) -> str:
    """The same timestamp text as in `services/office._ts`: the film reads the same clock as the
    office."""
    value = value.astimezone(dt.timezone.utc)
    return f"{value:%Y-%m-%dT%H:%M:%S}.{value.microsecond // 1000:03d}Z"


def _pl(n: int, ein: str, viele: str) -> str:
    return f"{n} {ein if n == 1 else viele}"


def datum_label(moment: dt.datetime) -> str:
    """"Wed 05.08.", in the zone `moment` brings along."""
    return f"{WOCHENTAGE[moment.weekday()]} {moment:%d.%m.}"


@dataclass
class Tagesbilanz:
    """What the day was: the numbers of the caption, computed in one place."""

    datum: str                      # "Wed 05.08.", local, built by Python
    laeufe: int = 0
    sitzungen: int = 0
    ereignisse: int = 0
    fehlschlaege: int = 0
    rueckfragen: int = 0
    kosten_usd: float = 0.0
    # As long as any cost entry has `priced IS NULL` (today that is all 411 of them), the sum is
    # a lower bound. The "≥" is then a duty, not decoration.
    kosten_partial: bool = False
    laengster: dict | None = None   # {"key", "titel", "minuten"}
    # The day had more events than a film can hold: the morning is missing.
    gekappt: bool = False


# ── The events of one day ───────────────────────────────────────────────────

async def tages_ereignisse(db: AsyncSession, *, von: dt.datetime,
                           bis: dt.datetime) -> tuple[list[dict], list[dict], Tagesbilanz]:
    """All events of a window, **across sessions** and strictly by `seq`.

    Returns (events, roster, summary). The roster is exactly the set of runs the summary counts
    over, and it has the shape of `agents[]` of the read API (`api/office._agent_row`). The
    renderer does not need it (it only gets `events`), but whoever wants to know WHO was in the
    room that day should not have to ask a second time and get a second set of runs.
    room that day should not have to ask a second time and get a second set of runs.

    `von` may (and should) stand in the zone of the job: the date label lives on that. For the
    query it is converted to UTC, because SQLite stores `DateTime` as a bare string **without** a
    zone, so a Berlin timestamp as a bind parameter would be compared against UTC strings there
    and would be two hours off in summer.
    """
    from ..api.office import _agent_row, _billed_by_run   # price and roster truth: ONE

    von_utc = von.astimezone(dt.timezone.utc)
    bis_utc = bis.astimezone(dt.timezone.utc)
    bilanz = Tagesbilanz(datum=datum_label(von))

    # Fetch descending and turn it around afterwards, the same truncation as in
    # `api/office.py`: the OLDEST is cut, because half a film would rather show the evening than
    # the morning. `cap + 1` reveals the truncation without a second COUNT.
    rows = (await db.execute(
        select(RunStep)
        .where(RunStep.created_at >= von_utc, RunStep.created_at < bis_utc)
        .order_by(RunStep.id.desc()).limit(EREIGNIS_CAP + 1)
    )).scalars().all()
    bilanz.gekappt = len(rows) > EREIGNIS_CAP
    schritte = sorted(rows[:EREIGNIS_CAP] if bilanz.gekappt else list(rows), key=lambda s: s.id)
    if not schritte:
        return [], [], bilanz

    run_ids = sorted({s.run_id for s in schritte})
    laeufe = (await db.execute(select(Run).where(Run.id.in_(run_ids)))).scalars().all()
    laeufe = sorted(laeufe, key=lambda r: r.id)

    issue_ids = {r.issue_id for r in laeufe if r.issue_id}
    tickets: dict[int, tuple[str, str]] = {}
    if issue_ids:
        tickets = {i: (k or "", s or "") for i, k, s in (await db.execute(
            select(Issue.id, Issue.key, Issue.summary).where(Issue.id.in_(issue_ids)))).all()}

    ctxs = {r.id: RunCtx.from_run(r, issue_key=tickets.get(r.issue_id or 0, ("", ""))[0])
            for r in laeufe}

    # Bounds of the window per run. Deliberately from the LOADED rows and not from a query of
    # its own over the whole run: whether a `run_start` exists has to refer to the window. A run
    # whose start row lies yesterday has no appearance in today's film, and without an added
    # boundary its agent would never sit at a desk.
    fenster: dict[int, dict] = {}
    for s in schritte:
        b = fenster.setdefault(s.run_id, {"erste": s.id, "letzte": s.id,
                                          "hat_start": False, "hat_ende": False})
        b["letzte"] = s.id
        art = (getattr(s, "kind", "") or "").strip()
        if art == "run_start":
            b["hat_start"] = True
        elif art == "run_end":
            b["hat_ende"] = True

    ereignisse: list[dict] = []
    for s in schritte:
        ctx = ctxs.get(s.run_id)
        if ctx is not None:
            ereignisse.extend(step_events(s, ctx))

    for run in laeufe:
        b = fenster.get(run.id)
        if b is None:
            continue
        grenzen = run_boundary_events(
            run, ctxs[run.id],
            first_step_id=None if b["hat_start"] else b["erste"],
            last_step_id=None if b["hat_ende"] else b["letzte"],
        )
        start = _utc(run.started_at)
        ende = _utc(run.finished_at) or start
        for ev in grenzen:
            if ev["kind"] == "run_start" and start is not None and start < von_utc:
                # Trap 1: the boundary carries `run.started_at`, so on a session across
                # midnight that is yesterday. Unclamped the HUD clock in the opening would show
                # the previous day, and that would look like a bug in the engine.
                ev["ts"] = _iso_ms(von_utc)
            elif ev["kind"] == "run_end" and ende is not None and ende >= bis_utc:
                # Trap 2: a run that only ends tomorrow has no end today. Filtering only here is
                # deliberate: before this it is not settled whether a `run_end` boundary appears
                # at all (a running run gets none).
                continue
            ereignisse.append(ev)

    # `seq` is the arrival order (`run_steps.id`), never `ts`. Across several sessions that holds
    # just as well: the row id is globally monotonic, so sorting yields ONE sequence for the
    # whole day and not twenty nested ones. On a tie the END goes before the beginning: first
    # somebody leaves the room, then the next one comes in.
    ereignisse.sort(key=lambda e: (e["seq"], 0 if e["kind"] == "run_end" else 1))
    _entdoppeln(ereignisse)
    # Trap 3 as a reminder: no `session_seen` is produced here. The film has one title and
    # chapter cards; twenty headers would be twenty titles for one day.

    prices = await PriceTable.load(db)
    billed = await _billed_by_run(db, run_ids, prices)
    roster = [_agent_row(r, billed.get(r.id)) for r in laeufe]

    bilanz.laeufe = len(laeufe)
    bilanz.sitzungen = len({ctx.sid for ctx in ctxs.values()})
    bilanz.ereignisse = len(ereignisse)
    bilanz.fehlschlaege = sum(1 for r in laeufe if (r.status or "") in FAIL_STATUS)
    bilanz.rueckfragen = sum(1 for r in laeufe if (r.status or "") in RUECKFRAGE_STATUS)
    bilanz.kosten_usd = round(sum(c["cost_usd"] for c in billed.values()), 6)
    # `_billed_by_run` resolves a NULL against TODAY's catalog, which is right for the question
    # "does this model have a price?". Under the film stands the sharper one: 411 of 413 cost
    # entries have `priced IS NULL`, so their amount came about without a recorded price, and a
    # catalog entry of today does not prove what held back then. The sum is a lower bound, and
    # the "≥" belongs in front of it. No second price computation, one count.
    offen = await db.scalar(select(func.count()).select_from(CostEntry).where(
        CostEntry.run_id.in_(run_ids), CostEntry.priced.is_(None)))
    bilanz.kosten_partial = any(not c["priced"] for c in billed.values()) or bool(offen)
    bilanz.laengster = _laengster(laeufe, tickets)
    return ereignisse, roster, bilanz


def _entdoppeln(ereignisse: list[dict]) -> int:
    """Resolve duplicate `seq`, **the** trap of the film across sessions.

    The body lives in `services/office.entdoppeln_seq`: since `GET /office/events` the room mixes
    several sessions into ONE log as well, and two resolutions of the same collision would be two
    tellings of the same transition. The name stays here, because the film
    dieser Stelle liest.
    """
    return entdoppeln_seq(ereignisse)


def _laengster(laeufe: list[Run], tickets: dict[int, tuple[str, str]]) -> dict | None:
    """The longest run of the day, with its **whole** duration and not with the part visible in
    the window. A run that ran 36.5 hours ran 36.5 hours; trimming it to the window would mean
    inventing a number nobody measured. Runs without an end (still running) stay out: their
    duration is not settled yet."""
    best: tuple[float, Run] | None = None
    for run in laeufe:
        start, ende = _utc(run.started_at), _utc(run.finished_at)
        if start is None or ende is None:
            continue
        dauer = (ende - start).total_seconds()
        if dauer <= 0:
            continue
        if best is None or dauer > best[0]:
            best = (dauer, run)
    if best is None:
        return None
    dauer, run = best
    key, titel = tickets.get(run.issue_id or 0, ("", ""))
    return {"key": key or (run.agent or f"Lauf {run.id}"), "titel": titel,
            "minuten": int(dauer // 60)}


# ── Bildunterschrift ────────────────────────────────────────────────────────

def _geld(betrag: float, *, partial: bool) -> str:
    """A decimal comma, and the "≥" where it belongs."""
    return ("≥ " if partial else "") + f"{betrag:.2f}".replace(".", ",") + " $"


def _dauer(minuten: int) -> str:
    """Bis anderthalb Stunden in Minuten, danach in Stunden — „2190 min" liest niemand."""
    if minuten < 90:
        return f"{minuten} min"
    return f"{minuten / 60:.1f}".replace(".", ",") + " h"


def bildunterschrift(bilanz: Tagesbilanz, *, kapitel: int, inseln: int, sekunden: int,
                     gekappt: bool) -> str:
    """The text under the film: short, at most 1024 characters (Telegram).

    Pure: no database, no clock. What the day was stands in the summary; what the film made of it
    comes from the response headers of the renderer. Empty statements fall away, because
    "0 failures · $0.00" is not a message but noise.
    """
    zeilen = [f"🎬 Feierabend · {bilanz.datum}",
              f"{_pl(bilanz.laeufe, 'Lauf', 'Läufe')} in "
              f"{_pl(bilanz.sitzungen, 'Sitzung', 'Sitzungen')} · "
              f"{_pl(bilanz.ereignisse, 'Ereignis', 'Ereignisse')}"]

    lage: list[str] = []
    if bilanz.fehlschlaege:
        lage.append(_pl(bilanz.fehlschlaege, "Fehlschlag", "Fehlschläge"))
    if bilanz.rueckfragen:
        lage.append(_pl(bilanz.rueckfragen, "Rückfrage", "Rückfragen"))
    if bilanz.kosten_usd > 0:
        lage.append(_geld(bilanz.kosten_usd, partial=bilanz.kosten_partial))
    if lage:
        zeilen.append(" · ".join(lage))

    lang = bilanz.laengster
    if lang:
        titel = (lang.get("titel") or "").strip()
        if len(titel) > TITEL_MAX:
            titel = titel[:TITEL_MAX - 1].rstrip() + "…"
        stueck = f"Längster: {lang['key']}"
        if titel:
            stueck += f" „{titel}“"
        zeilen.append(f"{stueck} · {_dauer(int(lang['minuten']))}")

    schluss = f"{kapitel} von {_pl(inseln, 'Szene', 'Szenen')} · {sekunden} s"
    if gekappt:
        # The renderer had to truncate: the morning is missing. That belongs under the film and
        # not only in the log, otherwise somebody takes the gap for a quiet day.
        schluss += " · gekappt"
    zeilen.append(schluss)

    text = "\n".join(zeilen)
    return text if len(text) <= UNTERTITEL_MAX else text[:UNTERTITEL_MAX - 1] + "…"


# ── The job ─────────────────────────────────────────────────────────────────

def _opt(job) -> dict:
    """The options from `job.args`. A LIST is the argument vector of a script job (`Job.args`
    carries both forms) and is simply empty for the film."""
    args = getattr(job, "args", None)
    return dict(args) if isinstance(args, dict) else {}


def _int(opt: dict, key: str, standard: int) -> int:
    try:
        return int(opt.get(key, standard))
    except (TypeError, ValueError):
        return standard


def _fenster(opt: dict) -> tuple[dt.datetime, dt.datetime]:
    """The office day: from local midnight until now.

    "End of day" means the job runs in the evening and shows the day behind us, hence
    `bis = jetzt` and not 24:00. A job running after midnight would film the fresh day; the
    schedule belongs in the evening.
    """
    name = str(opt.get("tz") or STD_TZ)
    try:
        from zoneinfo import ZoneInfo
        zone: dt.tzinfo = ZoneInfo(name)
    except Exception:  # noqa: BLE001 — fehlende tzdata darf keinen Film kosten
        log.warning("Time zone %s unknown, the film runs on UTC", name)
        zone = dt.timezone.utc
    jetzt = dt.datetime.now(tz=zone)
    return jetzt.replace(hour=0, minute=0, second=0, microsecond=0), jetzt


def _notification(*, kind: str, title: str, body: str, chat_id: str | None,
                  medium: str = "", medienart: str = "") -> Notification:
    """A notification, optionally with media.

    The two columns `media_path`/`media_kind` were added later. Until they exist (and on a
    backend whose migration is still pending) the film goes out as text: a missing column must
    not kill the job, it only costs the image.
    """
    n = Notification(kind=kind, title=title, body=body, chat_id=chat_id)
    if medium:
        if hasattr(Notification, "media_path"):
            n.media_path = medium
            n.media_kind = medienart
        else:
            log.warning("Notification without media columns, film %s stays text", medium)
    return n


async def _film_holen(payload: dict, *, timeout: float) -> tuple[int, bytes, dict]:
    """The call to the renderer. Built like `worker/runtime._do_screenshot`: one
    `httpx.AsyncClient`, one POST, bytes back. The filmer is the same case as the shotter, only
    with more images.

    `httpx` is imported only here: that way the test double takes effect, and the scheduler tick
    does not drag the network layer up at import time.
    """
    import httpx

    async with httpx.AsyncClient(timeout=timeout) as client:
        r = await client.post(f"{FILMER_URL.rstrip('/')}/film", json=payload)
    return r.status_code, r.content, dict(r.headers)


def _kopf(kopf: dict, name: str) -> str:
    """One response header, independent of its spelling. `httpx` returns the names in lower case,
    the renderer writes them as `X-Film-Kapitel`, so a direct `.get()` would find nothing and
    read every value as 0."""
    ziel = name.lower()
    for schluessel, wert in kopf.items():
        if str(schluessel).lower() == ziel:
            return str(wert)
    return ""


def _kopf_int(kopf: dict, name: str, standard: int = 0) -> int:
    try:
        return int(_kopf(kopf, name))
    except (TypeError, ValueError):
        return standard


def _kopf_ja(kopf: dict, name: str) -> bool:
    return _kopf(kopf, name).strip().lower() in ("1", "true", "ja", "yes")


def _aufraeumen(behalten_tage: int) -> int:
    """Clean up old films, in the **same** job. A second job for the same directory would be a
    second schedule that will eventually stand differently from this one."""
    if behalten_tage <= 0:
        return 0
    grenze = _now().timestamp() - behalten_tage * 86400
    weg = 0
    try:
        for name in os.listdir(FILM_DIR):
            if not (name.startswith("buero-") and name.endswith(".gif")):
                continue
            pfad = os.path.join(FILM_DIR, name)
            try:
                if os.path.getmtime(pfad) < grenze:
                    os.remove(pfad)
                    weg += 1
            except OSError:
                continue
    except OSError:
        return weg
    return weg


async def run_film_job(db: AsyncSession, job, jr) -> None:
    """kind=film: builds the daily film and puts it down as a notification with media.

    **A caveat that has to stay here:** `run_job_kind` runs *inline* in the scheduler tick
    (interval 15 s). 15 to 20 s of building the film hold the tick up, so while this job builds,
    no other job falls due. That is accepted deliberately (the precedent is `_run_script` with
    `run_timeout=600`), and that is why the httpx timeout lies below `job.run_timeout`: the job
    has to be able to write its own error, otherwise the JobRun would stay on "running" forever.

    Nothing escapes from here. An exception out of this branch would tear off the whole tick, and
    every other job due in that round would fall away with it, for one film.
    """
    opt = _opt(job)
    try:
        await _film_bauen(db, job, jr, opt)
    except Exception as e:  # noqa: BLE001 — bewusst alles
        jr.status, jr.error = "error", str(e)[:2000]
        log.exception("film-job %s fehlgeschlagen", getattr(job, "name", "?"))
    weg = _aufraeumen(_int(opt, "behalten_tage", STD_BEHALTEN_TAGE))
    if weg:
        jr.output = (jr.output or "") + f"\n{weg} alte Filme gelöscht."
    jr.finished_at = _now()


async def _film_bauen(db: AsyncSession, job, jr, opt: dict) -> None:
    von, bis = _fenster(opt)
    ereignisse, _roster, bilanz = await tages_ereignisse(db, von=von, bis=bis)
    # `notify_mode="never"` means "build it but do not send it" for the film: the file lies there
    # afterwards anyway. The finer modes (`on_output`/`on_error`) do not fit here: a film is
    # always output, and the distinction would be meaningless.
    still = job.notify_mode == "never"
    # The film belongs to whoever created the job, and the message is in their language.
    from ..models.user import User
    from .i18n import tr
    besitzer = await db.get(User, job.user_id) if getattr(job, "user_id", None) else None
    sprache = getattr(besitzer, "locale", None)

    if not ereignisse:
        # An explicit branch, not an emergency: an empty room would give 300 bit identical
        # frames. And no HTTP call, because the renderer would have nothing to render.
        jr.status = "ok"
        jr.output = f"{bilanz.datum}: keine Läufe — kein Film."
        if not still:
            db.add(_notification(
                kind="film",
                title=await tr(db, "server.notify.feierabend", sprache, datum=bilanz.datum),
                body=await tr(db, "server.notify.film_still", sprache),
                chat_id=job.notify_chat))
        return

    sekunden = _int(opt, "sekunden", STD_SEKUNDEN)
    fps = _int(opt, "fps", STD_FPS)
    payload = {
        "events": ereignisse,
        "grade": str(opt.get("grade") or STD_GRADE),
        "sekunden": sekunden,
        "fps": fps,
        "kapitel": _int(opt, "kapitel", STD_KAPITEL),
        # The renderer formats no time itself: it is told the offset.
        "tz_offset_min": int((von.utcoffset() or dt.timedelta()).total_seconds() // 60),
        "titel": bilanz.datum,
    }
    timeout = max(30.0, float(job.run_timeout or 600) - TIMEOUT_PUFFER_S)
    status, daten, kopf = await _film_holen(payload, timeout=timeout)

    if status == 204:
        # The renderer could not make a single scene out of the log. That is not an error, it is
        # the same quiet day, only this time it noticed.
        jr.status = "ok"
        jr.output = f"{bilanz.datum}: der Renderer fand keine Ereignisse (204)."
        if not still:
            db.add(_notification(
                kind="film",
                title=await tr(db, "server.notify.feierabend", sprache, datum=bilanz.datum),
                body=await tr(db, "server.notify.film_still", sprache),
                chat_id=job.notify_chat))
        return
    if status != 200 or not daten:
        jr.status = "error"
        jr.error = f"filmer HTTP {status}: {daten[:200].decode('utf-8', 'replace')}"
        return

    os.makedirs(FILM_DIR, exist_ok=True)
    pfad = os.path.join(FILM_DIR, f"buero-{von:%Y-%m-%d}.gif")
    with open(pfad, "wb") as fh:
        fh.write(daten)

    bilder = _kopf_int(kopf, KOPF_BILDER)
    untertitel = bildunterschrift(
        bilanz,
        kapitel=_kopf_int(kopf, KOPF_KAPITEL),
        inseln=_kopf_int(kopf, KOPF_INSELN),
        # How long the film really runs, not how long it was ordered: the cut lands on whole
        # frames and never hits the 25 s exactly. `X-Film-Dauer-Ms` is NOT good for that, it is
        # the build time of the renderer.
        sekunden=round(bilder / fps) if bilder and fps else sekunden,
        gekappt=bilanz.gekappt or _kopf_ja(kopf, KOPF_GEKAPPT),
    )
    # The bot assembles `<b>{title}</b>\n{body}`, so split at the first line that gives exactly
    # the caption again. A second title above the text would have shown the date twice.
    kopfzeile, _, rest = untertitel.partition("\n")
    if not still:
        db.add(_notification(kind="film", title=kopfzeile, body=rest,
                             chat_id=job.notify_chat, medium=pfad, medienart="animation"))
    jr.status = "ok"
    jr.output = (f"{bilanz.datum}: {_kopf_int(kopf, KOPF_KAPITEL)} von "
                 f"{_kopf_int(kopf, KOPF_INSELN)} Szenen, {bilder} Bilder, "
                 f"{len(daten) // 1024} kB in {_kopf_int(kopf, KOPF_DAUER)} ms → {pfad}")
