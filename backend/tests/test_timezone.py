"""Was heißt hier „8 Uhr"? — die Zeitzone der Person.

Geprüft wird die Mechanik an den zwei Stellen, an denen eine falsche Zone wirklich weh tut:
im Zeitplan eines Jobs und im Nachtfenster. Die Oberfläche rechnet mit derselben Angabe,
das lässt sich hier nicht prüfen.
"""
import datetime as dt
from zoneinfo import ZoneInfo

import pytest
from app.models.ops import Job
from app.services.scheduler import _due, zone_of

pytestmark = pytest.mark.asyncio

BERLIN = ZoneInfo("Europe/Berlin")


def _job(**fields) -> Job:
    return Job(name="morgens", type="cron", schedule="0 8 * * *", kind="prompt", **fields)


class Person:
    def __init__(self, zone: str):
        self.timezone = zone


async def test_cron_means_the_persons_clock_not_utc():
    """Sommerzeit: 8 Uhr in Berlin sind 6 Uhr UTC. Vorher lief der Job um 10."""
    job = _job(last_run_at=dt.datetime(2026, 7, 1, 4, 0, tzinfo=dt.timezone.utc))

    six_utc = dt.datetime(2026, 7, 1, 6, 1, tzinfo=dt.timezone.utc)
    assert _due(job, six_utc, BERLIN) is True, "8 Uhr Berlin ist erreicht"

    five_utc = dt.datetime(2026, 7, 1, 5, 30, tzinfo=dt.timezone.utc)
    assert _due(job, five_utc, BERLIN) is False, "erst halb acht in Berlin"


async def test_the_same_spec_in_another_zone():
    """Derselbe Plan, andere Person: der Job gehört ihrer Uhr, nicht der des Servers.

    8 Uhr in Tokio ist 23 Uhr UTC des Vortags — ein Job mit demselben Text läuft also zu
    einer ganz anderen Stunde des Servers, und genau so soll es sein.
    """
    tokio = ZoneInfo("Asia/Tokyo")
    job = _job(last_run_at=dt.datetime(2026, 6, 30, 20, 0, tzinfo=dt.timezone.utc))

    assert _due(job, dt.datetime(2026, 6, 30, 23, 1, tzinfo=dt.timezone.utc), tokio) is True
    assert _due(job, dt.datetime(2026, 6, 30, 22, 0, tzinfo=dt.timezone.utc), tokio) is False
    # Und in Berlin wäre derselbe Job zu dieser Stunde noch lange nicht dran.
    assert _due(job, dt.datetime(2026, 6, 30, 23, 1, tzinfo=dt.timezone.utc), BERLIN) is False


async def test_an_unknown_zone_halts_nothing():
    """Ein Tippfehler in der Zone darf keinen Zeitplan lahmlegen."""
    assert zone_of(Person("Erde/Mitte")) == ZoneInfo("Europe/Berlin")
    assert zone_of(Person("")) == ZoneInfo("Europe/Berlin")
    assert zone_of(None) == ZoneInfo("Europe/Berlin")
    assert zone_of(Person("Asia/Tokyo")) == ZoneInfo("Asia/Tokyo")


async def test_the_night_window_applies_in_the_persons_zone(db):
    """22 bis 6 ist keine Angabe in UTC, sondern im Alltag dessen, der schläft."""
    from app.services.agent_gate import zone_of as gate_zone

    from conftest import make_user
    anna = await make_user(db, "anna")
    anna.timezone = "Asia/Tokyo"
    await db.commit()
    assert gate_zone(anna) == ZoneInfo("Asia/Tokyo")
    # Vorgabe bleibt Berlin, solange niemand etwas anderes sagt.
    bert = await make_user(db, "bert")
    assert gate_zone(bert) == ZoneInfo("Europe/Berlin")


async def test_the_api_accepts_only_real_zones(db, client):
    from conftest import auth, make_user
    anna = await make_user(db, "anna")

    bad = await client.put("/me/timezone", headers=auth(anna), json={"value": "Erde/Mitte"})
    assert bad.status_code == 400

    good = await client.put("/me/timezone", headers=auth(anna), json={"value": "Asia/Tokyo"})
    assert good.status_code == 204
    await db.refresh(anna)
    assert anna.timezone == "Asia/Tokyo"

    me = await client.get("/auth/me", headers=auth(anna))
    assert me.json()["timezone"] == "Asia/Tokyo"
