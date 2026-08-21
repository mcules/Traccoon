"""As what mail was classified: counted, not collected.

The numbers come out of the rows that exist anyway, at query time. That is what makes the
answer cover the whole stock instead of starting at zero the day a counter was switched on,
and it is why a kind nobody put in a list shows up by itself.
"""
import datetime as dt

import pytest
from app.models.assistant import AssistantTask, SpamVerdict
from app.services.spam_report import classifications

from conftest import auth, make_user

pytestmark = pytest.mark.asyncio


async def _verdict(db, owner_id, kind, status, *, model_score=0.95, days=0) -> SpamVerdict:
    v = SpamVerdict(owner_user_id=owner_id, kind=kind, status=status,
                    model_score=model_score, sender_email="wer@spam.xyz", subject="x")
    db.add(v)
    await db.flush()
    if days:
        v.created_at = dt.datetime.now(tz=dt.timezone.utc) - dt.timedelta(days=days)
    await db.commit()
    return v


async def test_kinds_are_counted_unknown_ones_too(db):
    """`erpressung` stands in no list in the code. That is exactly the point."""
    anna = await make_user(db, "anna")
    await _verdict(db, anna.id, "phishing", "spam")
    await _verdict(db, anna.id, "phishing", "spam")
    await _verdict(db, anna.id, "phishing", "pending")
    await _verdict(db, anna.id, "werbung", "ham")
    await _verdict(db, anna.id, "erpressung", "spam")
    db.add(AssistantTask(owner_user_id=anna.id, kind="email", category="rechnung",
                         title="Rechnung"))
    await db.commit()

    data = await classifications(db, anna.id)

    assert data["kinds"]["phishing"] == {"total": 3, "sortedout": 2,
                                          "passed": 0, "open": 1}
    assert data["kinds"]["werbung"]["passed"] == 1
    assert data["kinds"]["erpressung"]["total"] == 1
    # The mail let through comes out of the second pot, otherwise one would count half a mailbox.
    assert data["kinds"]["rechnung"] == {"total": 1, "sortedout": 0,
                                          "passed": 1, "open": 0}
    # Sorted by size, so that the view has to sort nothing.
    assert list(data["kinds"])[0] == "phishing"


async def test_chat_does_not_count_as_mail(db):
    anna = await make_user(db, "anna")
    db.add(AssistantTask(owner_user_id=anna.id, kind="chat", category="", title="frage"))
    await db.commit()
    assert (await classifications(db, anna.id))["kinds"] == {}


async def test_old_lines_fall_out_of_the_window(db):
    anna = await make_user(db, "anna")
    await _verdict(db, anna.id, "phishing", "spam", days=60)
    assert (await classifications(db, anna.id, days=30))["kinds"] == {}
    assert (await classifications(db, anna.id, days=90))["kinds"]["phishing"]["total"] == 1


async def test_the_hit_rate_measures_only_what_was_decided(db):
    """An open question says nothing about who was right."""
    anna = await make_user(db, "anna")
    await _verdict(db, anna.id, "phishing", "spam", model_score=0.95)   # hit
    await _verdict(db, anna.id, "werbung", "ham", model_score=0.2)      # hit
    await _verdict(db, anna.id, "werbung", "ham", model_score=0.95)     # miss
    await _verdict(db, anna.id, "phishing", "pending", model_score=0.95)

    model = (await classifications(db, anna.id))["model"]
    assert model == {"decided": 3, "hits": 2, "quote": 0.667}


async def test_without_decisions_there_is_no_rate(db):
    anna = await make_user(db, "anna")
    await _verdict(db, anna.id, "phishing", "pending")
    assert (await classifications(db, anna.id))["model"]["quote"] is None


async def test_the_endpoint_delivers_both(db, client):
    anna = await make_user(db, "anna")
    await _verdict(db, anna.id, "phishing", "spam")
    r = await client.get("/assistant/stats?days=7", headers=auth(anna))
    assert r.status_code == 200
    data = r.json()
    assert data["days"] == 7
    assert data["kinds"]["phishing"]["sortedout"] == 1
    assert "verdicts" in data["operation"]
