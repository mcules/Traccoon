"""Feierabend-Film — der ganze Bürotag als Zeitraffer-GIF.

**Ein Film für den Tag, nicht einer je Sitzung.** Die Daten entscheiden das: ein Tag hat
15–44 Läufe in ebenso vielen Sitzungen, die meisten mit einem einzigen Lauf und rund
zwanzig Schritten. Ein Film je Sitzung wären zwanzig Filme mit je drei Bildern. Der Raum
mit zwölf Plätzen, in den Agenten hereinkommen und wieder gehen, **ist** der Tag — und
genau das ist die Erzählung, die ein Zeitraffer tragen kann.

**Unterhalb der HTTP-Schicht.** Die Ereignisse kommen aus `services/office.step_events`
und `run_boundary_events` — denselben Funktionen, durch die auch
`/office/sessions/…/events` geht. Kein neuer Endpunkt, keine zweite Deutung der
`run_steps`-Zeilen: was der Film zeigt, ist bitgleich das, was das Büro zeigt.

**Drei Fensterfallen**, alle drei real (die 36,5-Stunden-Sitzung von Lauf 404 gibt es):

1. Ein Lauf, der **vor** dem Fenster begann, hat seine `run_start`-Zeile draußen.
   `run_boundary_events` reicht sie nach — mit `run.started_at`, also mit gestrigem
   Zeitstempel. Der wird auf den Fensteranfang **geklemmt**, sonst zeigt die HUD-Uhr im
   Vorspann den Vortag und der Fehler sieht aus wie ein Engine-Fehler.
2. Ein Lauf, der **nach** dem Fenster endet, bekäme sein `run_end` mit morgigem
   Zeitstempel. Das wird **nach** dem Erzeugen gefiltert, nicht davor: vorher wüssten wir
   nicht, ob überhaupt eine Grenze entsteht (ein laufender Lauf bekommt gar keine).
3. `session_seen` wird hier **nicht** erzeugt. Die Lese-API setzt eine Kopfzeile je Raum;
   zwanzig Kopfzeilen in einem Film wären zwanzig Titel für einen Tag. Der Film trägt
   stattdessen Kapitelkarten, die der Renderer aus den Aktivitätsinseln schneidet.

**Der Ausgang ist der Notifier.** Dem backend-Container fehlt `TELEGRAM_BOT_TOKEN`
vollständig — nur `telegram-bot` spricht mit Telegram. Der Job schreibt deshalb eine
`Notification` mit `media_path`/`media_kind`, und der Bot verschickt sie. Ein zweiter
Ausgang wäre eine zweite Wahrheit darüber, wer was wann bekommen hat.

**Determinismus:** `grade` kommt aus den Job-Argumenten, nie aus der Uhr. **Alle**
Zeichenketten baut Python und schickt sie fertig mit (auch der Wochentag, siehe
`WOCHENTAGE`) — dann kann keine ICU-Version im Renderer das Bild verändern.
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
    EVENT_CAP_MAX, FAIL_STATUS, PriceTable, RunCtx, run_boundary_events, step_events,
)

log = logging.getLogger("office_film")

# Der Renderer ist ein Sidecar ohne Zugangsdaten: das Backend schickt den Log mit, der
# filmer holt nichts. Deshalb reicht ein nackter Dienstname ohne Auth.
FILMER_URL = os.getenv("FILMER_URL", "http://filmer:8710")
# Wo die fertigen Filme liegen. Muss in backend UND telegram-bot gemountet sein — der eine
# schreibt, der andere verschickt.
FILM_DIR = os.getenv("FILM_DIR", "/data/film")

# Vorgaben, wenn `job.args` schweigt.
STD_TZ = "Europe/Berlin"
STD_SEKUNDEN = 25
STD_FPS = 12
STD_GRADE = "night"
STD_KAPITEL = 8
STD_BEHALTEN_TAGE = 14

# Dieselbe Obergrenze, die der Renderer als `REPLAY_CAP` fährt. Der stärkste reale Tag
# hatte ~2500 Ereignisse; ein Ausreißertag darüber verlöre sonst **still** den Morgen.
# Gekappt wird vom ältesten Ende (wie in `api/office.py`), und die Bildunterschrift sagt es.
EREIGNIS_CAP = EVENT_CAP_MAX

# Ein Lauf, der auf einen Menschen wartet. Nicht mit „Fehlschlag" zusammenlegen: eine
# Rückfrage ist kein Scheitern, sondern eine offene Frage.
RUECKFRAGE_STATUS = ("blocked",)

# Wochentage selbst, nicht über `%a`: `strftime` ist locale-abhängig, und im Container ist
# das Locale C. „Wed" stünde dann unter einem deutschen Film.
WOCHENTAGE = ("Mo", "Di", "Mi", "Do", "Fr", "Sa", "So")

# Telegram schneidet eine Bildunterschrift bei 1024 Zeichen ab — lieber selbst kürzen als
# mitten im Wort abgeschnitten werden.
UNTERTITEL_MAX = 1024
TITEL_MAX = 60

# Antwortköpfe des Renderers. Nachgeschlagen wird ohne Rücksicht auf Groß-/Kleinschreibung
# (`_kopf`): httpx gibt die Namen kleingeschrieben zurück, der Renderer schreibt sie groß.
KOPF_KAPITEL = "X-Film-Kapitel"
KOPF_INSELN = "X-Film-Inseln"
KOPF_BILDER = "X-Film-Bilder"
KOPF_GEKAPPT = "X-Film-Gekappt"
# **Bauzeit**, nicht Spieldauer (`film.mjs`: `Date.now() - t0`). Wie lang der Film läuft,
# steht nirgends — das ist `Bilder / fps` und wird hier gerechnet.
KOPF_DAUER = "X-Film-Dauer-Ms"

# Wie viel Luft der HTTP-Aufruf unter `job.run_timeout` lässt. Der Job muss den Fehler noch
# selbst schreiben können; läuft ihm der Scheduler-Timeout zuvor, bliebe der JobRun auf
# „running" stehen.
TIMEOUT_PUFFER_S = 30


def _now() -> dt.datetime:
    return dt.datetime.now(tz=dt.timezone.utc)


def _utc(value: dt.datetime | None) -> dt.datetime | None:
    """Naive Zeitstempel als UTC lesen. SQLite liefert sie ohne Zone; ohne das wirft ein
    Vergleich zwischen zwei Zeilen je nach Datenbank einen TypeError oder Stunden."""
    if value is None:
        return None
    return value.replace(tzinfo=dt.timezone.utc) if value.tzinfo is None else value


def _iso_ms(value: dt.datetime) -> str:
    """Derselbe Zeitstempel-Text wie in `services/office._ts` — der Film liest dieselbe
    Uhr wie das Büro."""
    value = value.astimezone(dt.timezone.utc)
    return f"{value:%Y-%m-%dT%H:%M:%S}.{value.microsecond // 1000:03d}Z"


def _pl(n: int, ein: str, viele: str) -> str:
    return f"{n} {ein if n == 1 else viele}"


def datum_label(moment: dt.datetime) -> str:
    """„Mi 05.08." — in der Zone, die `moment` mitbringt."""
    return f"{WOCHENTAGE[moment.weekday()]} {moment:%d.%m.}"


@dataclass
class Tagesbilanz:
    """Was der Tag war — die Zahlen der Bildunterschrift, an einer Stelle gerechnet."""

    datum: str                      # „Mi 05.08.", lokal, von Python gebaut
    laeufe: int = 0
    sitzungen: int = 0
    ereignisse: int = 0
    fehlschlaege: int = 0
    rueckfragen: int = 0
    kosten_usd: float = 0.0
    # Solange irgendein Kostenposten `priced IS NULL` hat (das sind heute alle 411), ist
    # die Summe eine Untergrenze. Das „≥" ist dann Pflicht, keine Zierde.
    kosten_partial: bool = False
    laengster: dict | None = None   # {"key", "titel", "minuten"}
    # Der Tag hatte mehr Ereignisse, als ein Film fassen kann — der Morgen fehlt.
    gekappt: bool = False


# ── Die Ereignisse eines Tages ──────────────────────────────────────────────

async def tages_ereignisse(db: AsyncSession, *, von: dt.datetime,
                           bis: dt.datetime) -> tuple[list[dict], list[dict], Tagesbilanz]:
    """Alle Ereignisse eines Fensters, **sitzungsübergreifend** und streng nach `seq`.

    Rückgabe: (Ereignisse, Roster, Bilanz). Der Roster ist genau die Laufmenge, über die
    auch die Bilanz zählt, und hat die Form von `agents[]` der Lese-API
    (`api/office._agent_row`) — der Renderer braucht ihn nicht (er bekommt nur `events`),
    aber wer wissen will, WER an diesem Tag im Raum war, soll dafür nicht ein zweites Mal
    fragen und dabei eine zweite Laufmenge bekommen.

    `von` darf (und soll) in der Zone des Jobs stehen: davon lebt das Datums-Etikett.
    Für die Abfrage wird nach UTC umgerechnet — SQLite legt `DateTime` als nackte
    Zeichenkette **ohne** Zone ab, ein Berliner Zeitstempel als Bindeparameter würde
    dort gegen UTC-Zeichenketten verglichen und läge im Sommer zwei Stunden daneben.
    """
    from ..api.office import _agent_row, _billed_by_run   # Preis-/Rosterwahrheit: EINE

    von_utc = von.astimezone(dt.timezone.utc)
    bis_utc = bis.astimezone(dt.timezone.utc)
    bilanz = Tagesbilanz(datum=datum_label(von))

    # Absteigend holen und danach umdrehen — dieselbe Kappung wie in `api/office.py`:
    # abgeschnitten wird das ÄLTESTE, weil ein halber Film lieber den Abend zeigt als den
    # Morgen. `cap + 1` verrät die Kappung ohne ein zweites COUNT.
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

    # Grenzen des Fensters je Lauf. Bewusst aus den GELADENEN Zeilen und nicht aus einer
    # eigenen Abfrage über den ganzen Lauf: ob ein `run_start` existiert, muss sich auf das
    # Fenster beziehen. Ein Lauf, dessen Startzeile gestern liegt, hat im heutigen Film
    # keinen Auftritt — und ohne nachgereichte Grenze säße sein Agent nie am Schreibtisch.
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
                # Falle 1: die Grenze trägt `run.started_at` — bei einer Sitzung über
                # Mitternacht also gestern. Ungeklemmt zeigte die HUD-Uhr im Vorspann den
                # Vortag, und das sähe aus wie ein Fehler der Engine.
                ev["ts"] = _iso_ms(von_utc)
            elif ev["kind"] == "run_end" and ende is not None and ende >= bis_utc:
                # Falle 2: ein Lauf, der erst morgen endet, hat heute kein Ende. Erst hier
                # zu filtern ist Absicht — vorher steht nicht fest, ob überhaupt eine
                # `run_end`-Grenze entsteht (ein laufender Lauf bekommt keine).
                continue
            ereignisse.append(ev)

    # `seq` ist die Ankunftsreihenfolge (`run_steps.id`), nie `ts`. Über mehrere Sitzungen
    # hinweg gilt das genauso: die Zeilen-ID ist global monoton, also ergibt die Sortierung
    # EINE Folge für den ganzen Tag — und nicht zwanzig verschachtelte. Bei Gleichstand
    # geht das ENDE vor den Anfang: erst verlässt jemand den Raum, dann kommt der Nächste.
    ereignisse.sort(key=lambda e: (e["seq"], 0 if e["kind"] == "run_end" else 1))
    _entdoppeln(ereignisse)
    # Falle 3 zur Erinnerung: hier wird kein `session_seen` erzeugt. Der Film hat einen
    # Titel und Kapitelkarten; zwanzig Kopfzeilen wären zwanzig Titel für einen Tag.

    prices = await PriceTable.load(db)
    billed = await _billed_by_run(db, run_ids, prices)
    roster = [_agent_row(r, billed.get(r.id)) for r in laeufe]

    bilanz.laeufe = len(laeufe)
    bilanz.sitzungen = len({ctx.sid for ctx in ctxs.values()})
    bilanz.ereignisse = len(ereignisse)
    bilanz.fehlschlaege = sum(1 for r in laeufe if (r.status or "") in FAIL_STATUS)
    bilanz.rueckfragen = sum(1 for r in laeufe if (r.status or "") in RUECKFRAGE_STATUS)
    bilanz.kosten_usd = round(sum(c["cost_usd"] for c in billed.values()), 6)
    # `_billed_by_run` löst eine NULL gegen den HEUTIGEN Katalog auf — richtig für die
    # Frage „hat dieses Modell einen Preis?". Unter dem Film steht die schärfere: 411 von
    # 413 Kostenposten haben `priced IS NULL`, ihr Betrag entstand also ohne festgehaltenen
    # Preis, und ein Katalogeintrag von heute belegt nicht, was damals galt. Die Summe ist
    # eine Untergrenze, und das „≥" gehört davor. Keine zweite Preisrechnung — eine Zählung.
    offen = await db.scalar(select(func.count()).select_from(CostEntry).where(
        CostEntry.run_id.in_(run_ids), CostEntry.priced.is_(None)))
    bilanz.kosten_partial = any(not c["priced"] for c in billed.values()) or bool(offen)
    bilanz.laengster = _laengster(laeufe, tickets)
    return ereignisse, roster, bilanz


def _entdoppeln(ereignisse: list[dict]) -> int:
    """Doppelte `seq` auflösen — **die** Falle des sitzungsübergreifenden Films.

    Die nachgereichten Grenzen sitzen zwischen den Zeilen: `run_end` auf `letzte*4 + 3`,
    `run_start` auf `erste*4 - 1`. Das ist dieselbe Zahl, sobald der nächste Lauf mit der
    unmittelbar folgenden Zeilen-ID anfängt — und weil Läufe hintereinander laufen, ist
    das der Normalfall, nicht der Ausreißer (gemessen 13 Kollisionen an einem echten Tag
    mit 21 Läufen). In einer Sitzung fällt das kaum auf, im Tagesfilm trifft es fast jeden
    Übergang.

    Und es wäre nicht sichtbar, sondern still: der Recorder entdoppelt über `seq`
    (`office/recorder.ts`) und verwürfe das zweite Ereignis — ein Agent käme nie herein
    oder ginge nie. Deshalb rückt der Nachzügler auf die nächste freie Zahl. Die ist
    `erste*4 + 0` und damit der reservierte Slot 0 seiner eigenen ersten Zeile: noch immer
    vor deren Hauptereignis, also bleibt die Erzählung heil.

    Verschoben wird nur nach oben und nur bei Gleichstand — die Reihenfolge der bereits
    sortierten Liste bleibt dadurch unangetastet.
    """
    vorher = -1
    verschoben = 0
    for ev in ereignisse:
        if ev["seq"] <= vorher:
            ev["seq"] = vorher + 1
            verschoben += 1
        vorher = ev["seq"]
    return verschoben


def _laengster(laeufe: list[Run], tickets: dict[int, tuple[str, str]]) -> dict | None:
    """Der längste Lauf des Tages — mit seiner **ganzen** Dauer, nicht mit dem im Fenster
    sichtbaren Anteil. Ein Lauf, der 36,5 Stunden lief, lief 36,5 Stunden; ihn auf das
    Fenster zu beschneiden hieße, eine Zahl zu erfinden, die niemand gemessen hat.
    Läufe ohne Ende (noch am Laufen) bleiben draußen: ihre Dauer steht noch nicht fest."""
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
    """Deutsches Dezimalkomma, und das „≥" wo es hingehört."""
    return ("≥ " if partial else "") + f"{betrag:.2f}".replace(".", ",") + " $"


def _dauer(minuten: int) -> str:
    """Bis anderthalb Stunden in Minuten, danach in Stunden — „2190 min" liest niemand."""
    if minuten < 90:
        return f"{minuten} min"
    return f"{minuten / 60:.1f}".replace(".", ",") + " h"


def bildunterschrift(bilanz: Tagesbilanz, *, kapitel: int, inseln: int, sekunden: int,
                     gekappt: bool) -> str:
    """Der Text unter dem Film — deutsch, knapp, höchstens 1024 Zeichen (Telegram).

    Rein: keine DB, keine Uhr. Was der Tag war, steht in der Bilanz; was der Film daraus
    gemacht hat, kommt aus den Antwortköpfen des Renderers. Leere Aussagen fallen weg —
    „0 Fehlschläge · 0,00 $" ist keine Nachricht, sondern Rauschen.
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
        # Der Renderer musste kürzen: der Morgen fehlt. Das gehört unter den Film und
        # nicht nur ins Log — sonst hält jemand die Lücke für einen stillen Tag.
        schluss += " · gekappt"
    zeilen.append(schluss)

    text = "\n".join(zeilen)
    return text if len(text) <= UNTERTITEL_MAX else text[:UNTERTITEL_MAX - 1] + "…"


# ── Der Job ─────────────────────────────────────────────────────────────────

def _opt(job) -> dict:
    """Die Optionen aus `job.args`. Eine LISTE ist der Argumentvektor eines script-Jobs
    (`Job.args` trägt beide Formen) und für den Film schlicht leer."""
    args = getattr(job, "args", None)
    return dict(args) if isinstance(args, dict) else {}


def _int(opt: dict, key: str, standard: int) -> int:
    try:
        return int(opt.get(key, standard))
    except (TypeError, ValueError):
        return standard


def _fenster(opt: dict) -> tuple[dt.datetime, dt.datetime]:
    """Der Bürotag: von der lokalen Mitternacht bis jetzt.

    „Feierabend" heißt, dass der Job am Abend läuft und den Tag zeigt, der hinter uns
    liegt — deshalb `bis = jetzt` und nicht 24:00. Ein Job, der nach Mitternacht liefe,
    filmte damit den frischen Tag; der Zeitplan gehört an den Abend.
    """
    name = str(opt.get("tz") or STD_TZ)
    try:
        from zoneinfo import ZoneInfo
        zone: dt.tzinfo = ZoneInfo(name)
    except Exception:  # noqa: BLE001 — fehlende tzdata darf keinen Film kosten
        log.warning("Zeitzone %s unbekannt — Film läuft auf UTC", name)
        zone = dt.timezone.utc
    jetzt = dt.datetime.now(tz=zone)
    return jetzt.replace(hour=0, minute=0, second=0, microsecond=0), jetzt


def _notification(*, kind: str, title: str, body: str, chat_id: str | None,
                  medium: str = "", medienart: str = "") -> Notification:
    """Eine Notification, wahlweise mit Medium.

    Die beiden Spalten `media_path`/`media_kind` legt die Telegram-Welle an. Bis dahin
    (und auf einem Backend, dessen Migration noch aussteht) geht der Film eben als Text
    hinaus: eine fehlende Spalte darf den Job nicht töten, sie kostet nur das Bild.
    """
    n = Notification(kind=kind, title=title, body=body, chat_id=chat_id)
    if medium:
        if hasattr(Notification, "media_path"):
            n.media_path = medium
            n.media_kind = medienart
        else:
            log.warning("Notification ohne Medienspalten — Film %s bleibt Text", medium)
    return n


async def _film_holen(payload: dict, *, timeout: float) -> tuple[int, bytes, dict]:
    """Der Renderer-Aufruf. Gebaut wie `worker/runtime._do_screenshot`: ein
    `httpx.AsyncClient`, ein POST, Bytes zurück — der filmer ist derselbe Fall wie der
    shotter, nur mit mehr Bildern.

    `httpx` wird erst hier importiert: der Test-Ersatz greift so, und der Scheduler-Tick
    zieht die Netz-Schicht nicht schon beim Import mit hoch.
    """
    import httpx

    async with httpx.AsyncClient(timeout=timeout) as client:
        r = await client.post(f"{FILMER_URL.rstrip('/')}/film", json=payload)
    return r.status_code, r.content, dict(r.headers)


def _kopf(kopf: dict, name: str) -> str:
    """Ein Antwortkopf, unabhängig von der Schreibweise. `httpx` gibt die Namen
    kleingeschrieben zurück, der Renderer setzt sie in `X-Film-Kapitel`-Schreibweise —
    ein direktes `.get()` fände nichts und läse jeden Wert als 0."""
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
    """Alte Filme wegräumen — im **selben** Job. Ein zweiter Job für dasselbe Verzeichnis
    wäre ein zweiter Zeitplan, der irgendwann anders steht als dieser."""
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
    """kind=film: baut den Tagesfilm und legt ihn als Notification mit Medium hin.

    **Vorbehalt, der hier stehenbleiben muss:** `run_job_kind` läuft *inline* im
    Scheduler-Tick (Intervall 15 s). 15–20 s Filmbau halten den Tick auf — solange dieser
    Job baut, wird kein anderer Job fällig. Das ist bewusst in Kauf genommen (der
    Präzedenzfall ist `_run_script` mit `run_timeout=600`), und deshalb liegt der
    httpx-Timeout unter `job.run_timeout`: der Job muss seinen eigenen Fehler noch
    schreiben können, sonst bliebe der JobRun für immer auf „running".

    Es fliegt hier nichts heraus. Eine Ausnahme aus diesem Zweig risse den ganzen Tick
    ab — alle anderen fälligen Jobs dieser Runde fielen mit aus, für einen Film.
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
    # `notify_mode="never"` heißt beim Film „bau ihn, aber schick ihn nicht" — die Datei
    # liegt danach trotzdem da. Die feineren Modi (`on_output`/`on_error`) passen hier
    # nicht: ein Film ist immer Ausgabe, die Unterscheidung wäre bedeutungslos.
    still = job.notify_mode == "never"

    if not ereignisse:
        # Expliziter Zweig, kein Notfall: ein leerer Raum ergäbe 300 bitgleiche Bilder.
        # Und kein HTTP-Aufruf — der Renderer hätte nichts zu rendern.
        jr.status = "ok"
        jr.output = f"{bilanz.datum}: keine Läufe — kein Film."
        if not still:
            db.add(_notification(kind="film", title=f"Feierabend · {bilanz.datum}",
                                 body="🌙 Heute war es still im Büro — keine Läufe.",
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
        # Der Renderer formatiert keine Zeit selbst — er bekommt den Versatz mitgeteilt.
        "tz_offset_min": int((von.utcoffset() or dt.timedelta()).total_seconds() // 60),
        "titel": bilanz.datum,
    }
    timeout = max(30.0, float(job.run_timeout or 600) - TIMEOUT_PUFFER_S)
    status, daten, kopf = await _film_holen(payload, timeout=timeout)

    if status == 204:
        # Der Renderer hat aus dem Log keine einzige Szene machen können. Das ist kein
        # Fehler, es ist derselbe stille Tag — nur hat es diesmal er festgestellt.
        jr.status = "ok"
        jr.output = f"{bilanz.datum}: der Renderer fand keine Ereignisse (204)."
        if not still:
            db.add(_notification(kind="film", title=f"Feierabend · {bilanz.datum}",
                                 body="🌙 Heute war es still im Büro — keine Läufe.",
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
        # Wie lang der Film wirklich läuft, nicht wie lang er bestellt war: der Schnitt
        # landet auf ganzen Bildern und trifft die 25 s nie genau. `X-Film-Dauer-Ms` taugt
        # dafür NICHT — das ist die Bauzeit des Renderers.
        sekunden=round(bilder / fps) if bilder and fps else sekunden,
        gekappt=bilanz.gekappt or _kopf_ja(kopf, KOPF_GEKAPPT),
    )
    # Der Bot setzt `<b>{title}</b>\n{body}` zusammen — an der ersten Zeile getrennt
    # ergibt das wieder genau die Bildunterschrift. Ein zweiter Titel über dem Text hätte
    # das Datum doppelt gezeigt.
    kopfzeile, _, rest = untertitel.partition("\n")
    if not still:
        db.add(_notification(kind="film", title=kopfzeile, body=rest,
                             chat_id=job.notify_chat, medium=pfad, medienart="animation"))
    jr.status = "ok"
    jr.output = (f"{bilanz.datum}: {_kopf_int(kopf, KOPF_KAPITEL)} von "
                 f"{_kopf_int(kopf, KOPF_INSELN)} Szenen, {bilder} Bilder, "
                 f"{len(daten) // 1024} kB in {_kopf_int(kopf, KOPF_DAUER)} ms → {pfad}")
