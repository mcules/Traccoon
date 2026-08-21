"""The schedule of a job — and the mix-up that silences it quietly.

`type` is the schedule (cron/interval/once), `kind` the sort of work (workflow, film …). If a
sort accidentally stands in `type`, the job is never due: the UI still shows "enabled, every 15
minutes", and nothing happens. Exactly that way the job "Hermes-Posteingang" lay still for 13
days without anyone noticing.

Two safeguards against it — one that stops it coming into being, and one that makes existing
cases visible.
"""
import datetime as dt

import pytest

from app.models.ops import Job
from app.services.scheduler import SCHEDULE_KINDS, _due, _seconds
from conftest import auth, make_user


def _job(**fields) -> Job:
    reason = {"name": "Probe", "type": "interval", "schedule": "60", "kind": "workflow",
             "enabled": True, "last_run_at": None}
    return Job(**{**reason, "id": 1, **fields})


NOW = dt.datetime(2026, 8, 20, 12, 0, tzinfo=dt.timezone.utc)


# ── Due or not ──────────────────────────────────────────────────────────────

def test_an_interval_runs_after_the_waiting_time():
    job = _job(type="interval", schedule="900",
               last_run_at=NOW - dt.timedelta(seconds=899))
    assert _due(job, NOW) is False
    job.last_run_at = NOW - dt.timedelta(seconds=901)
    assert _due(job, NOW) is True


def test_an_interval_with_a_prefix_is_understood():
    """`interval:900` stands in older jobs and means the same as `900`."""
    assert _seconds("interval:900") == 900
    assert _seconds("900") == 900
    assert _seconds(" interval:900 ") == 900
    # Unlesbares ergibt eine Minute: lieber zu oft als nie.
    assert _seconds("alle 15 min") == 60
    assert _seconds("") == 60
    assert _seconds("0") == 60


def test_a_new_job_without_a_run_is_due_at_once():
    assert _due(_job(type="interval", schedule="900"), NOW) is True


def test_a_wrong_schedule_makes_the_job_silent(caplog):
    """The case this is about: a sort stands in `type` instead of a schedule."""
    job = _job(type="prompt", schedule="interval:900",
               last_run_at=NOW - dt.timedelta(days=13))
    with caplog.at_level("WARNING"):
        assert _due(job, NOW) is False
    # Silent it stays — but no longer unnoticed.
    assert "prompt" in caplog.text and "nie" in caplog.text


def test_cron_stays_untouched():
    job = _job(type="cron", schedule="0 6 * * *",
               last_run_at=NOW - dt.timedelta(days=1))
    assert _due(job, NOW) is True
    assert _due(_job(type="cron", schedule="kein cron"), NOW) is False


# ── It should not come into being in the first place ────────────────────────

async def test_a_job_with_a_kind_instead_of_a_schedule_is_rejected(client, db):
    user = await make_user(db, "chef")
    r = await client.post("/jobs", headers=auth(user), json={
        "name": "Posteingang", "type": "prompt", "schedule": "interval:900",
        "kind": "workflow"})
    assert r.status_code == 422
    assert "kind" in r.text


async def test_a_correct_schedule_passes(client, db):
    user = await make_user(db, "chef")
    for kind in SCHEDULE_KINDS:
        plan = {"cron": "0 6 * * *", "interval": "900",
                "once": "2026-12-24T18:00:00"}[kind]
        r = await client.post("/jobs", headers=auth(user), json={
            "name": f"Probe {kind}", "type": kind, "schedule": plan, "kind": "workflow"})
        assert r.status_code in (200, 201), r.text
