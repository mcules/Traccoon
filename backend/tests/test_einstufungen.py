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


async def _urteil(db, owner_id, art, status, *, model_score=0.95, tage=0) -> SpamVerdict:
    v = SpamVerdict(owner_user_id=owner_id, art=art, status=status,
                    model_score=model_score, sender_email="wer@spam.xyz", subject="x")
    db.add(v)
    await db.flush()
    if tage:
        v.created_at = dt.datetime.now(tz=dt.timezone.utc) - dt.timedelta(days=tage)
    await db.commit()
    return v


async def test_arten_werden_gezaehlt_auch_unbekannte(db):
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


async def test_chat_zaehlt_nicht_als_post(db):
    anna = await make_user(db, "anna")
    db.add(AssistantTask(owner_user_id=anna.id, kind="chat", category="", title="frage"))
    await db.commit()
    assert (await einstufungen(db, anna.id))["arten"] == {}


async def test_alte_zeilen_fallen_aus_dem_fenster(db):
    anna = await make_user(db, "anna")
    await _urteil(db, anna.id, "phishing", "spam", tage=60)
    assert (await einstufungen(db, anna.id, tage=30))["arten"] == {}
    assert (await einstufungen(db, anna.id, tage=90))["arten"]["phishing"]["gesamt"] == 1


async def test_trefferquote_misst_nur_entschiedenes(db):
    """Eine offene Rückfrage sagt nichts darüber, wer recht hatte."""
    anna = await make_user(db, "anna")
    await _urteil(db, anna.id, "phishing", "spam", model_score=0.95)   # Treffer
    await _urteil(db, anna.id, "werbung", "ham", model_score=0.2)      # Treffer
    await _urteil(db, anna.id, "werbung", "ham", model_score=0.95)     # daneben
    await _urteil(db, anna.id, "phishing", "pending", model_score=0.95)

    modell = (await einstufungen(db, anna.id))["modell"]
    assert modell == {"entschieden": 3, "treffer": 2, "quote": 0.667}


async def test_ohne_entscheidungen_gibt_es_keine_quote(db):
    anna = await make_user(db, "anna")
    await _urteil(db, anna.id, "phishing", "pending")
    assert (await einstufungen(db, anna.id))["modell"]["quote"] is None


async def test_endpunkt_liefert_beides(db, client):
    anna = await make_user(db, "anna")
    await _urteil(db, anna.id, "phishing", "spam")
    r = await client.get("/assistant/statistik?tage=7", headers=auth(anna))
    assert r.status_code == 200
    daten = r.json()
    assert daten["tage"] == 7
    assert daten["arten"]["phishing"]["aussortiert"] == 1
    assert "urteile" in daten["betrieb"]
