"""Der Zeitplan eines Jobs — und die Verwechslung, die ihn still stillegt.

`type` ist der Zeitplan (cron/interval/once), `kind` die Art der Arbeit (workflow, film …).
Steht in `type` versehentlich eine Art, ist der Job nie fällig: Die Oberfläche zeigt
weiterhin "eingeschaltet, alle 15 Minuten", und es passiert nichts. Genau so lag der Job
"Hermes-Posteingang" 13 Tage still, ohne dass es jemandem auffiel.

Zwei Sicherungen dagegen — eine, die es nicht mehr entstehen lässt, und eine, die vorhandene
Fälle sichtbar macht.
"""
import datetime as dt

import pytest

from app.models.ops import Job
from app.services.scheduler import ZEITPLAN_KINDS, _due, _seconds
from conftest import auth, make_user


def _job(**fields) -> Job:
    reason = {"name": "Probe", "type": "interval", "schedule": "60", "kind": "workflow",
             "enabled": True, "last_run_at": None}
    return Job(**{**reason, "id": 1, **fields})


NOW = dt.datetime(2026, 8, 20, 12, 0, tzinfo=dt.timezone.utc)


# ── Fällig oder nicht ────────────────────────────────────────────────────────

def test_intervall_running_nach_der_wartezeit():
    job = _job(type="interval", schedule="900",
               last_run_at=NOW - dt.timedelta(seconds=899))
    assert _due(job, NOW) is False
    job.last_run_at = NOW - dt.timedelta(seconds=901)
    assert _due(job, NOW) is True


def test_intervall_mit_praefix_wird_verstanden():
    """`interval:900` steht in älteren Jobs und meint dasselbe wie `900`."""
    assert _seconds("interval:900") == 900
    assert _seconds("900") == 900
    assert _seconds(" interval:900 ") == 900
    # Unlesbares ergibt eine Minute: lieber zu oft als nie.
    assert _seconds("alle 15 min") == 60
    assert _seconds("") == 60
    assert _seconds("0") == 60


def test_neuer_job_ohne_lauf_ist_sofort_due():
    assert _due(_job(type="interval", schedule="900"), NOW) is True


def test_falscher_zeitplan_macht_den_job_still(caplog):
    """Der Fall, um den es geht: In `type` steht eine Art statt eines Zeitplans."""
    job = _job(type="prompt", schedule="interval:900",
               last_run_at=NOW - dt.timedelta(days=13))
    with caplog.at_level("WARNING"):
        assert _due(job, NOW) is False
    # Still bleibt er — aber nicht mehr unbemerkt.
    assert "prompt" in caplog.text and "nie" in caplog.text


def test_cron_bleibt_unberuehrt():
    job = _job(type="cron", schedule="0 6 * * *",
               last_run_at=NOW - dt.timedelta(days=1))
    assert _due(job, NOW) is True
    assert _due(_job(type="cron", schedule="kein cron"), NOW) is False


# ── Es soll gar nicht erst entstehen ─────────────────────────────────────────

async def test_job_mit_kind_statt_zeitplan_wird_abgewiesen(client, db):
    user = await make_user(db, "chef")
    r = await client.post("/jobs", headers=auth(user), json={
        "name": "Posteingang", "type": "prompt", "schedule": "interval:900",
        "kind": "workflow"})
    assert r.status_code == 422
    assert "kind" in r.text


async def test_richtiger_zeitplan_geht_durch(client, db):
    user = await make_user(db, "chef")
    for kind in ZEITPLAN_KINDS:
        plan = {"cron": "0 6 * * *", "interval": "900",
                "once": "2026-12-24T18:00:00"}[kind]
        r = await client.post("/jobs", headers=auth(user), json={
            "name": f"Probe {kind}", "type": kind, "schedule": plan, "kind": "workflow"})
        assert r.status_code in (200, 201), r.text
