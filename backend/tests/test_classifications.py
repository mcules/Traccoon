"""As what mail was classified: counted, not collected.

The numbers come out of the rows that exist anyway, at query time. That is what makes the
answer cover the whole stock instead of starting at zero the day a counter was switched on,
and it is why a kind nobody put in a list shows up by itself.
"""
import datetime as dt

import pytest
from app.models.assistant import AssistantTask, SpamVerdict
from app.services.spam_report import einstufungen

from conftest import auth, make_user

pytestmark = pytest.mark.asyncio


async def _urteil(db, owner_id, kind, status, *, model_score=0.95, days=0) -> SpamVerdict:
    v = SpamVerdict(owner_user_id=owner_id, kind=kind, status=status,
                    model_score=model_score, sender_email="wer@spam.xyz", subject="x")
    db.add(v)
    await db.flush()
    if days:
        v.created_at = dt.datetime.now(tz=dt.timezone.utc) - dt.timedelta(days=days)
    await db.commit()
    return v


async def test_kinds_are_counted_unknown_ones_too(db):
    """`erpressung` steht in keiner Liste im Code. Genau darum geht es."""
    anna = await make_user(db, "anna")
    await _urteil(db, anna.id, "phishing", "spam")
    await _urteil(db, anna.id, "phishing", "spam")
    await _urteil(db, anna.id, "phishing", "pending")
    await _urteil(db, anna.id, "werbung", "ham")
    await _urteil(db, anna.id, "erpressung", "spam")
    db.add(AssistantTask(owner_user_id=anna.id, kind="email", category="rechnung",
                         title="Rechnung"))
    await db.commit()

    daten = await einstufungen(db, anna.id)

    assert daten["arten"]["phishing"] == {"gesamt": 3, "aussortiert": 2,
                                          "durchgelassen": 0, "offen": 1}
    assert daten["arten"]["werbung"]["durchgelassen"] == 1
    assert daten["arten"]["erpressung"]["gesamt"] == 1
    # Die durchgelassene Post kommt aus dem zweiten Topf, sonst zählte man ein halbes Postfach.
    assert daten["arten"]["rechnung"] == {"gesamt": 1, "aussortiert": 0,
                                          "durchgelassen": 1, "offen": 0}
    # Nach Größe sortiert, damit die Ansicht nichts sortieren muss.
    assert list(daten["arten"])[0] == "phishing"


async def test_chat_does_not_count_as_mail(db):
    anna = await make_user(db, "anna")
    db.add(AssistantTask(owner_user_id=anna.id, kind="chat", category="", title="frage"))
    await db.commit()
    assert (await einstufungen(db, anna.id))["arten"] == {}


async def test_old_lines_fall_out_of_the_window(db):
    anna = await make_user(db, "anna")
    await _urteil(db, anna.id, "phishing", "spam", days=60)
    assert (await einstufungen(db, anna.id, days=30))["arten"] == {}
    assert (await einstufungen(db, anna.id, days=90))["arten"]["phishing"]["gesamt"] == 1


async def test_the_hit_rate_measures_only_what_was_decided(db):
    """Eine offene Rückfrage sagt nichts darüber, wer recht hatte."""
    anna = await make_user(db, "anna")
    await _urteil(db, anna.id, "phishing", "spam", model_score=0.95)   # Treffer
    await _urteil(db, anna.id, "werbung", "ham", model_score=0.2)      # Treffer
    await _urteil(db, anna.id, "werbung", "ham", model_score=0.95)     # daneben
    await _urteil(db, anna.id, "phishing", "pending", model_score=0.95)

    modell = (await einstufungen(db, anna.id))["modell"]
    assert modell == {"entschieden": 3, "treffer": 2, "quote": 0.667}


async def test_without_decisions_there_is_no_rate(db):
    anna = await make_user(db, "anna")
    await _urteil(db, anna.id, "phishing", "pending")
    assert (await einstufungen(db, anna.id))["modell"]["quote"] is None


async def test_the_endpoint_delivers_both(db, client):
    anna = await make_user(db, "anna")
    await _urteil(db, anna.id, "phishing", "spam")
    r = await client.get("/assistant/stats?days=7", headers=auth(anna))
    assert r.status_code == 200
    daten = r.json()
    assert daten["tage"] == 7
    assert daten["arten"]["phishing"]["aussortiert"] == 1
    assert "urteile" in daten["betrieb"]
