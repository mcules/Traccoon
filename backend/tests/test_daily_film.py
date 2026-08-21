"""After-work film: the window, the caption and the outcome.

Four things are nailed down here because they would otherwise break silently:

1. **One day is ONE sequence.** Two sessions in the same window give a single series
   ascending by `seq`, not two interleaved ones. If the film sorted by `ts`, it would show
   an order that never existed under `WORKER_CONCURRENCY > 1`.
2. **The two window traps of the 36.5 hour session.** A run that began before the window
   gets its added `run_start` with yesterday's timestamp (clamped); one that ends after the
   window would get a `run_end` from tomorrow (which drops out).
3. **A quiet day does not call the renderer at all.** An empty room would give 300 bit
   identical frames, and an HTTP call producing them would be 20 s of tick standstill for nothing.
4. **A dead filmer does not topple the tick.** The job reports the error on its JobRun, and
   the other due jobs of the same round run regardless.
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

# The window of all tests: one full day in UTC. Deliberately hard wired: a window derived
# from the clock would make the test depend on the time of day.
FROM = dt.datetime(2026, 8, 5, 0, 0, tzinfo=dt.timezone.utc)
TO = dt.datetime(2026, 8, 6, 0, 0, tzinfo=dt.timezone.utc)
MITTAG = dt.datetime(2026, 8, 5, 12, 0, tzinfo=dt.timezone.utc)


# ── Testdaten ────────────────────────────────────────────────────────────────

async def ticket(db, project, number: int, summary: str = "Tu was") -> Issue:
    kind = IssueType(project_id=project.id, name=f"Aufgabe {number}")
    status = WorkflowStatus(project_id=project.id, name=f"To Do {number}",
                            category=StatusCategory.todo)
    db.add_all([kind, status])
    await db.commit()
    i = Issue(project_id=project.id, number=number, key=f"{project.key}-{number}",
              type_id=kind.id, status_id=status.id, summary=summary, reporter_id=1, rank="1")
    db.add(i)
    await db.commit()
    return i


async def make_run(db, *, issue=None, project=None, agent="developer", status="success",
               start=None, end=None) -> Run:
    r = Run(issue_id=issue.id if issue else None,
            project_id=project.id if project else (issue.project_id if issue else None),
            agent=agent, phase="execute", provider="claude_code", model="sonnet",
            status=status, started_at=start or MITTAG,
            finished_at=end if end is not None else (
                None if status == "running" else MITTAG + dt.timedelta(minutes=5)))
    db.add(r)
    await db.commit()
    return r


async def step(db, run: Run, *, kind: str = "agent_text", when=None, text="hallo") -> RunStep:
    s = RunStep(run_id=run.id, seq=1, role="assistant", kind=kind, content=text,
                created_at=when or MITTAG)
    db.add(s)
    await db.commit()
    return s


def kinds(events: list[dict], kind: str) -> list[dict]:
    return [e for e in events if e["kind"] == kind]


# ── The window ───────────────────────────────────────────────────────────────

async def test_two_sessions_yield_one_ascending_sequence(db):
    """The room with twelve seats IS the day: two tickets, one film, one sequence.

    The steps are deliberately written interleaved here (A, B, A, B), exactly as they come
    into being under parallel workers. If a block of its own came out per session, the
    figures in the film would jump back and forth.
    """
    p = await make_project(db, "TRA", "Traccoon")
    a = await make_run(db, issue=await ticket(db, p, 1, "Erstes"))
    b = await make_run(db, issue=await ticket(db, p, 2, "Zweites"))
    for i, run in enumerate((a, b, a, b)):
        await step(db, run, when=MITTAG + dt.timedelta(seconds=i))

    events, roster, balance = await of.daily_events(db, von=FROM, to=TO)

    seqs = [e["seq"] for e in events]
    assert seqs == sorted(seqs)
    assert len(set(seqs)) == len(seqs)          # no two events on the same step
    assert len({e["sid"] for e in events}) == 2
    assert balance.runs == 2 and balance.sessions == 2
    assert len(roster) == 2
    # Trap 3: the read API sets one header per room. Twenty of them would be twenty titles
    # for one day, so the film carries chapter cards.
    assert kinds(events, "session_seen") == []


async def test_consecutive_runs_do_not_collide_on_the_same_seq(db):
    """The transition from run to run is the place where the day film breaks.

    `run_end` sits on `letzte*4 + 3`, `run_start` on `erste*4 - 1`: the same number as soon
    as the next run begins with the immediately following row id. With runs going one after
    another that is exactly the normal case (on a real day: 13 collisions with 21 runs). The
    recorder deduplicates over `seq` and would discard the second event, so an agent would
    never come in or never leave, without a single error message.
    """
    p = await make_project(db, "TRA", "Traccoon")
    last = None
    for n in range(1, 6):
        r = await make_run(db, issue=await ticket(db, p, n),
                       start=MITTAG + dt.timedelta(minutes=n),
                       end=MITTAG + dt.timedelta(minutes=n, seconds=30))
        last = await step(db, r, when=MITTAG + dt.timedelta(minutes=n))
        assert last is not None

    events, _r, _b = await of.daily_events(db, von=FROM, to=TO)

    seqs = [e["seq"] for e in events]
    assert len(set(seqs)) == len(seqs), "duplicate seq, the recorder discards the second one"
    assert seqs == sorted(seqs)
    # All five agents come in AND leave again.
    assert len(kinds(events, "run_start")) == 5
    assert len(kinds(events, "run_end")) == 5
    # And on a tie the end goes before the start: first the seat becomes free.
    series = [e["kind"] for e in events if e["kind"] in ("run_start", "run_end")]
    assert series[1:3] == ["run_end", "run_start"]


async def test_a_run_before_the_window_clamps_the_start_to_the_window_begin(db):
    """The 36.5 hour session, first half: the run began yesterday.

    `run_boundary_events` adds the missing `run_start`, with `run.started_at`. Unclamped,
    the HUD clock in the opening would show the previous day, and the error would look like
    an engine bug.
    """
    p = await make_project(db, "TRA", "Traccoon")
    r = await make_run(db, issue=await ticket(db, p, 1),
                   start=FROM - dt.timedelta(hours=4), end=MITTAG)
    await step(db, r, when=MITTAG)

    events, _roster, _balance = await of.daily_events(db, von=FROM, to=TO)

    starts = kinds(events, "run_start")
    assert len(starts) == 1
    assert starts[0]["ts"] == of._iso_ms(FROM)
    # And the boundary stays at the front: the agent comes in before it speaks.
    assert starts[0]["seq"] < min(e["seq"] for e in kinds(events, "agent_text"))


async def test_a_start_inside_the_window_stays_untouched(db):
    """Only what lies outside is clamped; otherwise every run would begin at midnight."""
    p = await make_project(db, "TRA", "Traccoon")
    r = await make_run(db, issue=await ticket(db, p, 1), start=MITTAG, end=MITTAG + dt.timedelta(minutes=5))
    await step(db, r, when=MITTAG + dt.timedelta(minutes=1))

    events, _r, _b = await of.daily_events(db, von=FROM, to=TO)
    assert kinds(events, "run_start")[0]["ts"] == of._iso_ms(MITTAG)


async def test_a_run_past_the_window_loses_its_end(db):
    """The 36.5 hour session, second half: the run only ends tomorrow.

    Its `run_end` would carry a timestamp from tomorrow. Filtering happens **after**
    producing: beforehand it is not even settled whether a boundary comes into being (a
    running run gets none). The run in the window beside it keeps its own.
    """
    p = await make_project(db, "TRA", "Traccoon")
    over = await make_run(db, issue=await ticket(db, p, 1), start=MITTAG,
                       end=TO + dt.timedelta(hours=9))
    inside = await make_run(db, issue=await ticket(db, p, 2), start=MITTAG,
                      end=MITTAG + dt.timedelta(minutes=5))
    await step(db, over, when=MITTAG)
    await step(db, inside, when=MITTAG)

    events, _roster, _balance = await of.daily_events(db, von=FROM, to=TO)

    ends = kinds(events, "run_end")
    assert [e["run_id"] for e in ends] == [inside.id]
    # The run is in the film regardless: it did work, it only does not stop today.
    assert over.id in {e["run_id"] for e in events}


async def test_steps_outside_the_window_are_missing(db):
    """The window cuts the steps, not the runs."""
    p = await make_project(db, "TRA", "Traccoon")
    r = await make_run(db, issue=await ticket(db, p, 1), start=FROM - dt.timedelta(days=1),
                   end=MITTAG)
    await step(db, r, when=FROM - dt.timedelta(hours=2), text="gestern")
    await step(db, r, when=MITTAG, text="heute")

    events, _r, balance = await of.daily_events(db, von=FROM, to=TO)
    texts = [e["text"] for e in kinds(events, "agent_text")]
    assert texts == ["heute"]
    assert balance.events == len(events)


async def test_the_balance_counts_failures_questions_and_cost(db):
    """Failure and question are two things: an open question is not a failure."""
    p = await make_project(db, "TRA", "Traccoon")
    bad = await make_run(db, issue=await ticket(db, p, 1), status="failed")
    question = await make_run(db, issue=await ticket(db, p, 2), status="blocked")
    good = await make_run(db, issue=await ticket(db, p, 3), status="success")
    for r in (bad, question, good):
        await step(db, r)
    # `priced=None` is the existing data: 411 of 411 cost entries. Without a catalog entry
    # the sum stays a lower bound, hence the "≥".
    db.add(CostEntry(run_id=good.id, provider="claude_code", model="sonnet",
                     cost_usd=1.83, priced=None))
    await db.commit()

    _e, _r, balance = await of.daily_events(db, von=FROM, to=TO)
    assert (balance.failures, balance.questions) == (1, 1)
    assert balance.cost_usd == pytest.approx(1.83)
    assert balance.cost_partial is True


# ── Bildunterschrift ─────────────────────────────────────────────────────────

def balance(**fields) -> of.Dailybalance:
    return of.Dailybalance(date="Mi 05.08.", **fields)


def test_a_caption_for_a_full_day():
    text = of.caption(
        balance(runs=19, sessions=19, events=609, failures=2, questions=1,
               cost_usd=1.83, cost_partial=True,
               longest={"key": "TRA-412", "titel": "Büro aufräumen", "minuten": 47}),
        chapter=8, islands=67, seconds=24, capped=False)
    assert text == ("🎬 Feierabend · Mi 05.08.\n"
                    "19 Läufe in 19 Sitzungen · 609 Ereignisse\n"
                    "2 Fehlschläge · 1 Rückfrage · ≥ 1,83 $\n"
                    "Längster: TRA-412 „Büro aufräumen“ · 47 min\n"
                    "8 von 67 Szenen · 24 s")


def test_a_caption_in_the_singular():
    """One run, one session, one event, one failure, one scene."""
    text = of.caption(
        balance(runs=1, sessions=1, events=1, failures=1, questions=1),
        chapter=1, islands=1, seconds=3, capped=False)
    assert "1 Lauf in 1 Sitzung · 1 Ereignis" in text
    assert "1 Fehlschlag · 1 Rückfrage" in text
    assert "1 von 1 Szene · 3 s" in text


def test_a_caption_leaves_empty_statements_out():
    """0 failures, $0.00 and no longest run: then nothing of that stands there either.
    "0 failures · $0.00" is not news but noise."""
    text = of.caption(
        balance(runs=3, sessions=2, events=40), chapter=2, islands=5, seconds=10,
        capped=False)
    assert text.splitlines() == ["🎬 Feierabend · Mi 05.08.",
                                 "3 Läufe in 2 Sitzungen · 40 Ereignisse",
                                 "2 von 5 Szenen · 10 s"]
    assert "$" not in text and "Längster" not in text


def test_a_caption_reports_truncation_and_stays_below_1024():
    long = {"key": "TRA-1", "titel": "x" * 400, "minuten": 2190}
    text = of.caption(balance(runs=900, sessions=900, events=99999,
                                      longest=long),
                               chapter=8, islands=140, seconds=25, capped=True)
    assert text.endswith("· gekappt")
    assert len(text) <= of.SUBTITLE_MAX
    assert "36,5 h" in text          # 2190 min liest niemand


# ── The job ──────────────────────────────────────────────────────────────────

class FakeAnswer:
    def __init__(self, status_code: int, content: bytes, headers: dict):
        self.status_code, self.content, self.headers = status_code, content, headers


@pytest.fixture
def filmer(monkeypatch):
    """The renderer as a dummy, plus a counter that proves whether it was called.

    What is patched is `httpx.AsyncClient` itself and not the caller: that way the real code
    including its error handling runs through the tests.
    """
    # The headers come LOWER CASED, exactly as `httpx` returns them, while the renderer sets
    # them as "X-Film-Kapitel". With the constants as keys the test would run green and
    # reality would read 0 everywhere.
    state = {"aufrufe": [], "antwort": FakeAnswer(200, b"GIF89a-film", {
        "content-type": "image/gif", "x-film-kapitel": "8", "x-film-inseln": "67",
        # 293 frames at 12 fps = 24.4 s of playing time. `x-film-dauer-ms` is the BUILD time
        # of the renderer (`film.mjs`: `Date.now() - t0`) and is no good for that.
        "x-film-bilder": "293", "x-film-gekappt": "0", "x-film-dauer-ms": "884"}),
        "fehler": None}

    class FakeClient:
        def __init__(self, *a, **k):
            state["timeout"] = k.get("timeout")

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def post(self, url, json=None):
            state["aufrufe"].append((url, json))
            if state["fehler"] is not None:
                raise state["fehler"]
            return state["antwort"]

    monkeypatch.setattr(httpx, "AsyncClient", FakeClient)
    return state


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


async def test_a_quiet_day_without_media_and_without_http(db, filmer, monkeypatch, tmp_path):
    """A day without runs: a message, no film, and **no** renderer call.

    The call is the point: 300 bit identical frames of an empty room cost 20 s of tick
    standstill and say nothing.
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


async def test_the_film_is_built_and_stored_as_media(db, filmer, monkeypatch, tmp_path):
    """The good case: a GIF on disk, the caption on the notification, media kind `animation`
    (not `video`, not `photo`: Telegram shows only those as an animation)."""
    monkeypatch.setattr(of, "FILM_DIR", str(tmp_path))
    p = await make_project(db, "TRA", "Traccoon")
    r = await make_run(db, issue=await ticket(db, p, 412, "Büro aufräumen"))
    await step(db, r)
    job, jr = await film_job(db)

    monkeypatch.setattr(of, "_window", lambda opt: (FROM, TO))
    await of.run_film_job(db, job, jr)
    await db.commit()

    (url, payload), = filmer["aufrufe"]
    assert url.endswith("/film")
    assert payload["grade"] == "night" and payload["fps"] == 12
    assert payload["titel"] == "Mi 05.08." and payload["tz_offset_min"] == 0
    assert [e["kind"] for e in payload["events"]][:1] == ["run_start"]
    # The httpx timeout has to lie BELOW `job.run_timeout`; otherwise the job no longer
    # writes its own error.
    assert filmer["timeout"] < job.run_timeout

    assert jr.status == "ok"
    assert (tmp_path / "buero-2026-08-05.gif").read_bytes() == b"GIF89a-film"
    n = (await notifications(db))[0]
    # The bot assembles `<b>{title}</b>\n{body}`, which together gives exactly the caption,
    # without showing the date twice.
    assert f"{n.title}\n{n.body}".startswith("🎬 Feierabend · Mi 05.08.")
    # 8 chapters out of 67 islands, and the playing time from frames over fps (293/12), not
    # the ordered 25 s and certainly not the 884 ms of build time.
    assert "8 von 67 Szenen · 24 s" in n.body
    if hasattr(Notification, "media_path"):     # the columns are created by the Telegram wave
        assert n.media_path == str(tmp_path / "buero-2026-08-05.gif")
        assert n.media_kind == "animation"


async def test_an_unreachable_filmer_does_not_topple_the_tick(db, filmer, monkeypatch, tmp_path):
    """The renderer is dead: an error on the JobRun, no media notification, and the tick runs
    on. If the exception flew out, all other due jobs of the same round would drop out with
    it, for one film."""
    monkeypatch.setattr(of, "FILM_DIR", str(tmp_path))
    monkeypatch.setattr(of, "_window", lambda opt: (FROM, TO))
    filmer["fehler"] = httpx.ConnectError("Verbindung abgelehnt")
    p = await make_project(db, "TRA", "Traccoon")
    await step(db, await make_run(db, issue=await ticket(db, p, 1)))
    await film_job(db)

    # The whole tick, not only the branch: that is the place where it would hurt.
    await scheduler._tick()

    jr = (await db.execute(select(JobRun).order_by(JobRun.id.desc()))).scalars().first()
    assert jr.status == "error" and "Verbindung abgelehnt" in jr.error
    assert jr.finished_at is not None
    assert await notifications(db) == []
    assert list(tmp_path.iterdir()) == []


async def test_the_film_stays_its_own_kind(db, filmer, monkeypatch, tmp_path):
    """`run_job_kind` ist die einzige Stelle, an der `kind` verzweigt — und von den fünf
    Arten sind nur der Film und der Ablauf übrig. Ohne den Zweig liefe der Job ins Leere."""
    monkeypatch.setattr(of, "FILM_DIR", str(tmp_path))
    job, jr = await film_job(db)
    await scheduler.run_job_kind(db, job, jr)
    assert jr.finished_at is not None and jr.status in ("ok", "error")


async def test_prune_deletes_only_old_films(monkeypatch, tmp_path):
    """Cleaning up belongs in THIS job: a second one for the same directory would be a second
    schedule that stands differently at some point."""
    import os
    monkeypatch.setattr(of, "FILM_DIR", str(tmp_path))
    old, new, foreign = (tmp_path / "buero-2026-07-01.gif", tmp_path / "buero-2026-08-05.gif",
                       tmp_path / "notizen.txt")
    for f in (old, new, foreign):
        f.write_bytes(b"x")
    daybeforeyesterday = dt.datetime.now().timestamp() - 30 * 86400
    os.utime(old, (daybeforeyesterday, daybeforeyesterday))
    os.utime(foreign, (daybeforeyesterday, daybeforeyesterday))

    assert of._prune(14) == 1
    assert not old.exists() and new.exists() and foreign.exists()
    # 0 days = never delete, the same reading as `run_retention_days`.
    assert of._prune(0) == 0
