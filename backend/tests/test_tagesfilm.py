"""Feierabend-Film: das Fenster, die Bildunterschrift und der Ausgang.

Vier Dinge werden hier festgenagelt, weil sie sonst still kaputtgehen:

1. **Ein Tag ist EINE Folge.** Zwei Sitzungen im selben Fenster ergeben eine einzige
   nach `seq` aufsteigende Reihe — nicht zwei verschachtelte. Sortierte der Film nach
   `ts`, zeigte er unter `WORKER_CONCURRENCY > 1` eine Reihenfolge, die es nie gab.
2. **Die zwei Fensterfallen der 36,5-Stunden-Sitzung.** Ein Lauf, der vor dem Fenster
   begann, bekommt sein nachgereichtes `run_start` mit gestrigem Zeitstempel (geklemmt);
   einer, der nach dem Fenster endet, bekäme ein `run_end` von morgen (fällt raus).
3. **Ein stiller Tag ruft den Renderer gar nicht.** Ein leerer Raum ergäbe 300 bitgleiche
   Bilder — und ein HTTP-Aufruf, der sie erzeugt, wäre 20 s Tick-Stillstand für nichts.
4. **Ein toter filmer kippt den Tick nicht.** Der Job meldet den Fehler an seinem JobRun,
   und die anderen fälligen Jobs derselben Runde laufen trotzdem.
"""
import datetime as dt

import httpx
import pytest
from sqlalchemy import select

from app.models.agents import CostEntry, Run, RunStep
from app.models.enums import StatusCategory
from app.models.notification import Notification
from app.models.ops import Job, JobRun
from app.models.ticket import Issue, IssueCounter, IssueType, WorkflowStatus
from app.services import office_film as of
from app.services import scheduler
from conftest import make_project

# Das Fenster aller Tests: ein voller Tag in UTC. Bewusst fest verdrahtet — ein Fenster,
# das sich aus der Uhr ergibt, macht den Test von der Tageszeit abhängig.
VON = dt.datetime(2026, 8, 5, 0, 0, tzinfo=dt.timezone.utc)
BIS = dt.datetime(2026, 8, 6, 0, 0, tzinfo=dt.timezone.utc)
MITTAG = dt.datetime(2026, 8, 5, 12, 0, tzinfo=dt.timezone.utc)


# ── Testdaten ────────────────────────────────────────────────────────────────

async def ticket(db, projekt, nummer: int, summary: str = "Tu was") -> Issue:
    typ = IssueType(project_id=projekt.id, name=f"Aufgabe {nummer}")
    status = WorkflowStatus(project_id=projekt.id, name=f"To Do {nummer}",
                            category=StatusCategory.todo)
    db.add_all([typ, status])
    await db.commit()
    i = Issue(project_id=projekt.id, number=nummer, key=f"{projekt.key}-{nummer}",
              type_id=typ.id, status_id=status.id, summary=summary, reporter_id=1, rank="1")
    db.add(i)
    await db.commit()
    return i


async def lauf(db, *, issue=None, projekt=None, agent="developer", status="success",
               start=None, ende=None) -> Run:
    r = Run(issue_id=issue.id if issue else None,
            project_id=projekt.id if projekt else (issue.project_id if issue else None),
            agent=agent, phase="execute", provider="claude_code", model="sonnet",
            status=status, started_at=start or MITTAG,
            finished_at=ende if ende is not None else (
                None if status == "running" else MITTAG + dt.timedelta(minutes=5)))
    db.add(r)
    await db.commit()
    return r


async def schritt(db, run: Run, *, kind: str = "agent_text", wann=None, text="hallo") -> RunStep:
    s = RunStep(run_id=run.id, seq=1, role="assistant", kind=kind, content=text,
                created_at=wann or MITTAG)
    db.add(s)
    await db.commit()
    return s


def arten(ereignisse: list[dict], art: str) -> list[dict]:
    return [e for e in ereignisse if e["kind"] == art]


# ── Das Fenster ──────────────────────────────────────────────────────────────

async def test_zwei_sitzungen_ergeben_eine_aufsteigende_folge(db):
    """Der Raum mit zwölf Plätzen IST der Tag: zwei Tickets, ein Film, eine Folge.

    Die Schritte werden hier absichtlich verschränkt geschrieben (A, B, A, B) — genau so
    entstehen sie unter parallelen Workern. Käme je Sitzung ein eigener Block heraus,
    sprängen die Figuren im Film hin und her.
    """
    p = await make_project(db, "TRA", "Traccoon")
    a = await lauf(db, issue=await ticket(db, p, 1, "Erstes"))
    b = await lauf(db, issue=await ticket(db, p, 2, "Zweites"))
    for i, run in enumerate((a, b, a, b)):
        await schritt(db, run, wann=MITTAG + dt.timedelta(seconds=i))

    ereignisse, roster, bilanz = await of.tages_ereignisse(db, von=VON, bis=BIS)

    seqs = [e["seq"] for e in ereignisse]
    assert seqs == sorted(seqs)
    assert len(set(seqs)) == len(seqs)          # keine zwei Ereignisse auf derselben Stufe
    assert len({e["sid"] for e in ereignisse}) == 2
    assert bilanz.laeufe == 2 and bilanz.sitzungen == 2
    assert len(roster) == 2
    # Falle 3: die Lese-API setzt je Raum eine Kopfzeile. Zwanzig davon wären zwanzig
    # Titel für einen Tag — der Film trägt Kapitelkarten.
    assert arten(ereignisse, "session_seen") == []


async def test_aufeinanderfolgende_laeufe_kollidieren_nicht_auf_derselben_seq(db):
    """Der Übergang von Lauf zu Lauf ist die Stelle, an der der Tagesfilm bricht.

    `run_end` sitzt auf `letzte*4 + 3`, `run_start` auf `erste*4 - 1` — dieselbe Zahl,
    sobald der nächste Lauf mit der unmittelbar folgenden Zeilen-ID anfängt. Genau das ist
    bei nacheinander laufenden Läufen der Normalfall (an einem echten Tag: 13 Kollisionen
    bei 21 Läufen). Der Recorder entdoppelt über `seq` und verwürfe das zweite Ereignis —
    ein Agent käme nie herein oder ginge nie, ohne eine einzige Fehlermeldung.
    """
    p = await make_project(db, "TRA", "Traccoon")
    letzte = None
    for n in range(1, 6):
        r = await lauf(db, issue=await ticket(db, p, n),
                       start=MITTAG + dt.timedelta(minutes=n),
                       ende=MITTAG + dt.timedelta(minutes=n, seconds=30))
        letzte = await schritt(db, r, wann=MITTAG + dt.timedelta(minutes=n))
        assert letzte is not None

    ereignisse, _r, _b = await of.tages_ereignisse(db, von=VON, bis=BIS)

    seqs = [e["seq"] for e in ereignisse]
    assert len(set(seqs)) == len(seqs), "doppelte seq — der Recorder verwirft die zweite"
    assert seqs == sorted(seqs)
    # Alle fünf Agenten kommen herein UND gehen wieder.
    assert len(arten(ereignisse, "run_start")) == 5
    assert len(arten(ereignisse, "run_end")) == 5
    # Und beim Gleichstand geht das Ende vor den Anfang: erst wird der Platz frei.
    reihe = [e["kind"] for e in ereignisse if e["kind"] in ("run_start", "run_end")]
    assert reihe[1:3] == ["run_end", "run_start"]


async def test_lauf_vor_dem_fenster_klemmt_den_start_auf_den_fensteranfang(db):
    """Die 36,5-Stunden-Sitzung, erste Hälfte: der Lauf begann gestern.

    `run_boundary_events` reicht das fehlende `run_start` nach — mit `run.started_at`.
    Ungeklemmt zeigte die HUD-Uhr im Vorspann den Vortag, und der Fehler sähe aus wie
    ein Engine-Fehler.
    """
    p = await make_project(db, "TRA", "Traccoon")
    r = await lauf(db, issue=await ticket(db, p, 1),
                   start=VON - dt.timedelta(hours=4), ende=MITTAG)
    await schritt(db, r, wann=MITTAG)

    ereignisse, _roster, _bilanz = await of.tages_ereignisse(db, von=VON, bis=BIS)

    starts = arten(ereignisse, "run_start")
    assert len(starts) == 1
    assert starts[0]["ts"] == of._iso_ms(VON)
    # Und die Grenze bleibt vorn: der Agent kommt herein, bevor er redet.
    assert starts[0]["seq"] < min(e["seq"] for e in arten(ereignisse, "agent_text"))


async def test_start_im_fenster_bleibt_unangetastet(db):
    """Geklemmt wird nur, was draußen liegt — sonst begänne jeder Lauf um Mitternacht."""
    p = await make_project(db, "TRA", "Traccoon")
    r = await lauf(db, issue=await ticket(db, p, 1), start=MITTAG, ende=MITTAG + dt.timedelta(minutes=5))
    await schritt(db, r, wann=MITTAG + dt.timedelta(minutes=1))

    ereignisse, _r, _b = await of.tages_ereignisse(db, von=VON, bis=BIS)
    assert arten(ereignisse, "run_start")[0]["ts"] == of._iso_ms(MITTAG)


async def test_lauf_nach_dem_fenster_verliert_sein_ende(db):
    """Die 36,5-Stunden-Sitzung, zweite Hälfte: der Lauf endet erst morgen.

    Sein `run_end` trüge einen morgigen Zeitstempel. Gefiltert wird **nach** dem Erzeugen
    — vorher steht gar nicht fest, ob eine Grenze entsteht (ein laufender Lauf bekommt
    keine). Der Lauf im Fenster daneben behält seine.
    """
    p = await make_project(db, "TRA", "Traccoon")
    ueber = await lauf(db, issue=await ticket(db, p, 1), start=MITTAG,
                       ende=BIS + dt.timedelta(hours=9))
    drin = await lauf(db, issue=await ticket(db, p, 2), start=MITTAG,
                      ende=MITTAG + dt.timedelta(minutes=5))
    await schritt(db, ueber, wann=MITTAG)
    await schritt(db, drin, wann=MITTAG)

    ereignisse, _roster, _bilanz = await of.tages_ereignisse(db, von=VON, bis=BIS)

    enden = arten(ereignisse, "run_end")
    assert [e["run_id"] for e in enden] == [drin.id]
    # Der Lauf ist trotzdem im Film — er hat ja gearbeitet, er hört nur nicht heute auf.
    assert ueber.id in {e["run_id"] for e in ereignisse}


async def test_schritte_ausserhalb_des_fensters_fehlen(db):
    """Das Fenster schneidet die Schritte, nicht die Läufe."""
    p = await make_project(db, "TRA", "Traccoon")
    r = await lauf(db, issue=await ticket(db, p, 1), start=VON - dt.timedelta(days=1),
                   ende=MITTAG)
    await schritt(db, r, wann=VON - dt.timedelta(hours=2), text="gestern")
    await schritt(db, r, wann=MITTAG, text="heute")

    ereignisse, _r, bilanz = await of.tages_ereignisse(db, von=VON, bis=BIS)
    texte = [e["text"] for e in arten(ereignisse, "agent_text")]
    assert texte == ["heute"]
    assert bilanz.ereignisse == len(ereignisse)


async def test_bilanz_zaehlt_fehlschlaege_rueckfragen_und_kosten(db):
    """Fehlschlag und Rückfrage sind zwei Dinge: eine offene Frage ist kein Scheitern."""
    p = await make_project(db, "TRA", "Traccoon")
    schlecht = await lauf(db, issue=await ticket(db, p, 1), status="failed")
    frage = await lauf(db, issue=await ticket(db, p, 2), status="blocked")
    gut = await lauf(db, issue=await ticket(db, p, 3), status="success")
    for r in (schlecht, frage, gut):
        await schritt(db, r)
    # `priced=None` ist der Bestand: 411 von 411 Kostenposten. Ohne Katalogeintrag bleibt
    # die Summe eine Untergrenze — daher das „≥".
    db.add(CostEntry(run_id=gut.id, provider="claude_code", model="sonnet",
                     cost_usd=1.83, priced=None))
    await db.commit()

    _e, _r, bilanz = await of.tages_ereignisse(db, von=VON, bis=BIS)
    assert (bilanz.fehlschlaege, bilanz.rueckfragen) == (1, 1)
    assert bilanz.kosten_usd == pytest.approx(1.83)
    assert bilanz.kosten_partial is True


# ── Bildunterschrift ─────────────────────────────────────────────────────────

def bilanz(**felder) -> of.Tagesbilanz:
    return of.Tagesbilanz(datum="Mi 05.08.", **felder)


def test_bildunterschrift_voller_tag():
    text = of.bildunterschrift(
        bilanz(laeufe=19, sitzungen=19, ereignisse=609, fehlschlaege=2, rueckfragen=1,
               kosten_usd=1.83, kosten_partial=True,
               laengster={"key": "TRA-412", "titel": "Büro aufräumen", "minuten": 47}),
        kapitel=8, inseln=67, sekunden=24, gekappt=False)
    assert text == ("🎬 Feierabend · Mi 05.08.\n"
                    "19 Läufe in 19 Sitzungen · 609 Ereignisse\n"
                    "2 Fehlschläge · 1 Rückfrage · ≥ 1,83 $\n"
                    "Längster: TRA-412 „Büro aufräumen“ · 47 min\n"
                    "8 von 67 Szenen · 24 s")


def test_bildunterschrift_singular():
    """Ein Lauf, eine Sitzung, ein Ereignis, ein Fehlschlag, eine Szene."""
    text = of.bildunterschrift(
        bilanz(laeufe=1, sitzungen=1, ereignisse=1, fehlschlaege=1, rueckfragen=1),
        kapitel=1, inseln=1, sekunden=3, gekappt=False)
    assert "1 Lauf in 1 Sitzung · 1 Ereignis" in text
    assert "1 Fehlschlag · 1 Rückfrage" in text
    assert "1 von 1 Szene · 3 s" in text


def test_bildunterschrift_laesst_leere_aussagen_weg():
    """0 Fehlschläge, 0,00 $ und kein längster Lauf: dann steht davon auch nichts da.
    „0 Fehlschläge · 0,00 $" ist keine Nachricht, sondern Rauschen."""
    text = of.bildunterschrift(
        bilanz(laeufe=3, sitzungen=2, ereignisse=40), kapitel=2, inseln=5, sekunden=10,
        gekappt=False)
    assert text.splitlines() == ["🎬 Feierabend · Mi 05.08.",
                                 "3 Läufe in 2 Sitzungen · 40 Ereignisse",
                                 "2 von 5 Szenen · 10 s"]
    assert "$" not in text and "Längster" not in text


def test_bildunterschrift_meldet_kappung_und_bleibt_unter_1024():
    lang = {"key": "TRA-1", "titel": "x" * 400, "minuten": 2190}
    text = of.bildunterschrift(bilanz(laeufe=900, sitzungen=900, ereignisse=99999,
                                      laengster=lang),
                               kapitel=8, inseln=140, sekunden=25, gekappt=True)
    assert text.endswith("· gekappt")
    assert len(text) <= of.UNTERTITEL_MAX
    assert "36,5 h" in text          # 2190 min liest niemand


# ── Der Job ──────────────────────────────────────────────────────────────────

class FakeAntwort:
    def __init__(self, status_code: int, content: bytes, headers: dict):
        self.status_code, self.content, self.headers = status_code, content, headers


@pytest.fixture
def filmer(monkeypatch):
    """Der Renderer als Attrappe — und ein Zähler, der beweist, ob er gerufen wurde.

    Gepatcht wird `httpx.AsyncClient` selbst und nicht der Aufrufer: so läuft der echte
    Code inklusive seiner Fehlerbehandlung durch die Tests.
    """
    # Die Köpfe kommen KLEINGESCHRIEBEN — genau so gibt `httpx` sie zurück, während der
    # Renderer sie als „X-Film-Kapitel" setzt. Mit den Konstanten als Schlüssel liefe der
    # Test grün und die Wirklichkeit läse überall 0.
    zustand = {"aufrufe": [], "antwort": FakeAntwort(200, b"GIF89a-film", {
        "content-type": "image/gif", "x-film-kapitel": "8", "x-film-inseln": "67",
        # 293 Bilder bei 12 fps = 24,4 s Spielzeit. `x-film-dauer-ms` ist die BAUZEIT des
        # Renderers (`film.mjs`: `Date.now() - t0`) und taugt dafür nicht.
        "x-film-bilder": "293", "x-film-gekappt": "0", "x-film-dauer-ms": "884"}),
        "fehler": None}

    class FakeClient:
        def __init__(self, *a, **k):
            zustand["timeout"] = k.get("timeout")

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def post(self, url, json=None):
            zustand["aufrufe"].append((url, json))
            if zustand["fehler"] is not None:
                raise zustand["fehler"]
            return zustand["antwort"]

    monkeypatch.setattr(httpx, "AsyncClient", FakeClient)
    return zustand


async def film_job(db, *, notify_chat="4711") -> tuple[Job, JobRun]:
    job = Job(name="Feierabend", type="interval", schedule="60", kind="film",
              notify_chat=notify_chat, run_timeout=600,
              args={"tz": "UTC", "sekunden": 25, "fps": 12, "grade": "night",
                    "kapitel": 8, "behalten_tage": 14})
    db.add(job)
    await db.commit()
    jr = JobRun(job_id=job.id, status="running")
    db.add(jr)
    await db.commit()
    return job, jr


async def notifications(db) -> list[Notification]:
    return list((await db.execute(select(Notification).order_by(Notification.id))).scalars().all())


async def test_stiller_tag_ohne_medium_und_ohne_http(db, filmer, monkeypatch, tmp_path):
    """Ein Tag ohne Läufe: eine Nachricht, kein Film — und **kein** Renderer-Aufruf.

    Der Aufruf ist der Punkt: 300 bitgleiche Bilder von einem leeren Raum kosten 20 s
    Tick-Stillstand und sagen nichts.
    """
    monkeypatch.setattr(of, "FILM_DIR", str(tmp_path))
    job, jr = await film_job(db)
    await of.run_film_job(db, job, jr)
    await db.commit()

    assert filmer["aufrufe"] == []
    assert jr.status == "ok" and jr.finished_at is not None
    n = (await notifications(db))[0]
    assert n.body == "🌙 Heute war es still im Büro — keine Läufe."
    assert getattr(n, "media_path", None) is None


async def test_film_wird_gebaut_und_als_medium_hinterlegt(db, filmer, monkeypatch, tmp_path):
    """Der gute Fall: GIF auf Platte, Bildunterschrift an der Notification, Medienart
    `animation` (nicht `video`, nicht `photo` — Telegram zeigt nur die als Animation)."""
    monkeypatch.setattr(of, "FILM_DIR", str(tmp_path))
    p = await make_project(db, "TRA", "Traccoon")
    r = await lauf(db, issue=await ticket(db, p, 412, "Büro aufräumen"))
    await schritt(db, r)
    job, jr = await film_job(db)

    monkeypatch.setattr(of, "_fenster", lambda opt: (VON, BIS))
    await of.run_film_job(db, job, jr)
    await db.commit()

    (url, payload), = filmer["aufrufe"]
    assert url.endswith("/film")
    assert payload["grade"] == "night" and payload["fps"] == 12
    assert payload["titel"] == "Mi 05.08." and payload["tz_offset_min"] == 0
    assert [e["kind"] for e in payload["events"]][:1] == ["run_start"]
    # Der httpx-Timeout muss UNTER `job.run_timeout` liegen, sonst schreibt der Job
    # seinen eigenen Fehler nicht mehr.
    assert filmer["timeout"] < job.run_timeout

    assert jr.status == "ok"
    assert (tmp_path / "buero-2026-08-05.gif").read_bytes() == b"GIF89a-film"
    n = (await notifications(db))[0]
    # Der Bot setzt `<b>{title}</b>\n{body}` zusammen — zusammen ergibt das genau die
    # Bildunterschrift, ohne das Datum doppelt zu zeigen.
    assert f"{n.title}\n{n.body}".startswith("🎬 Feierabend · Mi 05.08.")
    # 8 Kapitel aus 67 Inseln, und die Spielzeit aus Bildern/fps (293/12), nicht die
    # bestellten 25 s und erst recht nicht die 884 ms Bauzeit.
    assert "8 von 67 Szenen · 24 s" in n.body
    if hasattr(Notification, "media_path"):     # die Spalten legt die Telegram-Welle an
        assert n.media_path == str(tmp_path / "buero-2026-08-05.gif")
        assert n.media_kind == "animation"


async def test_filmer_nicht_erreichbar_kippt_den_tick_nicht(db, filmer, monkeypatch, tmp_path):
    """Der Renderer ist tot: Fehler am JobRun, keine Medien-Notification — und der Tick
    läuft weiter. Flöge die Ausnahme heraus, fielen alle anderen fälligen Jobs derselben
    Runde mit aus, für einen Film."""
    monkeypatch.setattr(of, "FILM_DIR", str(tmp_path))
    monkeypatch.setattr(of, "_fenster", lambda opt: (VON, BIS))
    filmer["fehler"] = httpx.ConnectError("Verbindung abgelehnt")
    p = await make_project(db, "TRA", "Traccoon")
    await schritt(db, await lauf(db, issue=await ticket(db, p, 1)))
    await film_job(db)

    # Der ganze Tick, nicht nur der Zweig: das ist die Stelle, an der es wehtäte.
    await scheduler._tick()

    jr = (await db.execute(select(JobRun).order_by(JobRun.id.desc()))).scalars().first()
    assert jr.status == "error" and "Verbindung abgelehnt" in jr.error
    assert jr.finished_at is not None
    assert await notifications(db) == []
    assert list(tmp_path.iterdir()) == []


async def test_film_ist_die_vierte_art_von_run_job_kind(db, filmer, monkeypatch, tmp_path):
    """`run_job_kind` ist die einzige Stelle, an der `kind` verzweigt — und sie kennt
    `film`. Ohne den Zweig liefe der Job als leerer Prompt beim Assistenten."""
    monkeypatch.setattr(of, "FILM_DIR", str(tmp_path))
    job, jr = await film_job(db)
    assert await scheduler.run_job_kind(db, job, jr) is True


async def test_aufraeumen_loescht_nur_alte_filme(monkeypatch, tmp_path):
    """Aufräumen gehört in DIESEN Job: ein zweiter für dasselbe Verzeichnis wäre ein
    zweiter Zeitplan, der irgendwann anders steht."""
    import os
    monkeypatch.setattr(of, "FILM_DIR", str(tmp_path))
    alt, neu, fremd = (tmp_path / "buero-2026-07-01.gif", tmp_path / "buero-2026-08-05.gif",
                       tmp_path / "notizen.txt")
    for f in (alt, neu, fremd):
        f.write_bytes(b"x")
    vorgestern = dt.datetime.now().timestamp() - 30 * 86400
    os.utime(alt, (vorgestern, vorgestern))
    os.utime(fremd, (vorgestern, vorgestern))

    assert of._aufraeumen(14) == 1
    assert not alt.exists() and neu.exists() and fremd.exists()
    # 0 Tage = nie löschen, dieselbe Lesart wie `run_retention_days`.
    assert of._aufraeumen(0) == 0
