"""Lehrstoff aus den Postfächern: was schon entschieden ist, muss nicht gefragt werden.

Geprüft wird die Mechanik, die dabei leicht schiefgeht: dass kein Durchlauf doppelt zählt,
dass nur tragfähige Merkmale gelernt werden — und dass fremde Empfänger aus Massenversand
nicht ins Gedächtnis wandern.
"""
import pytest
from app.models.assistant import SpamFeatureStat
from app.services import spam_bootstrap, spam_learn
from app.services.appsettings import get_setting, set_setting
from app.services.spam_review import MEINE_ADRESSEN_KEY
from sqlalchemy import select

from conftest import make_user


@pytest.fixture
async def anna(db):
    user = await make_user(db, "anna")
    await set_setting(db, MEINE_ADRESSEN_KEY, "ich@meine-domain.de, *@alias.example")
    return user


def _treffer(uid: int, addr: str, subject: str, to: str = "ich@meine-domain.de") -> dict:
    return {"uid": uid, "from": [{"name": "", "addr": addr}],
            "to": [{"name": "", "addr": to}], "cc": [], "subject": subject,
            "message_id": f"<{uid}@x>", "date": "", "flags": []}


@pytest.fixture
def imap(monkeypatch):
    """`imap-mcp` durch einen Ordner-Bestand ersetzen: {(konto, ordner): [treffer, …]}."""
    bestand: dict[tuple[str, str], list[dict]] = {}
    konten = [{"alias": "privat", "inbox_folder": "INBOX", "spam_folder": "Junk"}]
    ordner = {"privat": ["INBOX", "INBOX/Bewerbung", "Archives/2025", "Archives/2009",
                         "Sent", "Drafts", "Junk"]}

    async def fake_call_tool(url, tool, arguments, **kw):
        if tool == "list_accounts":
            return {"structuredContent": {"accounts": konten}}
        if tool == "list_folders":
            return {"structuredContent": {"folders": [
                {"name": n, "ignored": False} for n in ordner[arguments["account"]]]}}
        if tool == "search_emails":
            treffer = bestand.get((arguments["account"], arguments["folder"]), [])
            return {"structuredContent": {"results": treffer[-arguments["limit"]:]}}
        raise AssertionError(f"unerwartetes Werkzeug {tool}")

    monkeypatch.setattr(spam_bootstrap, "call_tool", fake_call_tool)
    return bestand


async def _zaehler(db, feature: str) -> tuple[int, int]:
    row = (await db.execute(select(SpamFeatureStat).where(
        SpamFeatureStat.feature == feature))).scalars().first()
    return ((row.spam_count, row.ham_count) if row else (0, 0))


async def test_spam_ordner_wird_zu_lehrstoff(db, anna, imap):
    imap[("privat", "Junk")] = [_treffer(1, "wer@zufall.top", "Gewinn abholen")]
    gelesen, gelernt = await spam_bootstrap.nachlernen(
        db, anna.id, "privat", "Junk", ist_spam=True)
    assert (gelesen, gelernt) == (1, 1)
    assert await _zaehler(db, "from:wer@zufall.top") == (1, 0)
    assert await _zaehler(db, "dom:zufall.top") == (1, 0)


async def test_zweiter_lauf_zaehlt_nicht_doppelt(db, anna, imap):
    imap[("privat", "Junk")] = [_treffer(1, "wer@zufall.top", "Gewinn abholen")]
    await spam_bootstrap.nachlernen(db, anna.id, "privat", "Junk", ist_spam=True)
    gelesen, gelernt = await spam_bootstrap.nachlernen(
        db, anna.id, "privat", "Junk", ist_spam=True)
    assert (gelesen, gelernt) == (1, 0)
    assert await _zaehler(db, "from:wer@zufall.top") == (1, 0)

    # Erst was NEU dazukommt, zählt wieder.
    imap[("privat", "Junk")].append(_treffer(2, "wer@zufall.top", "Noch ein Gewinn"))
    _, gelernt = await spam_bootstrap.nachlernen(db, anna.id, "privat", "Junk", ist_spam=True)
    assert gelernt == 1
    assert await _zaehler(db, "from:wer@zufall.top") == (2, 0)
    assert await get_setting(db, "spam_lernstand:privat:Junk") == "2"


async def test_fremde_empfaenger_werden_nicht_gelernt(db, anna, imap):
    """Massenversand trägt fremde Adressen in An/CC — die tauchen nie wieder auf."""
    imap[("privat", "Junk")] = [
        _treffer(7, "wer@zufall.top", "Angebot", to="fremde.person@woanders.de")]
    await spam_bootstrap.nachlernen(db, anna.id, "privat", "Junk", ist_spam=True)
    assert await _zaehler(db, "to:fremde.person@woanders.de") == (0, 0)
    assert await _zaehler(db, "from:wer@zufall.top") == (1, 0)


async def test_alias_wird_aus_dem_nachlauf_nicht_gelernt(db, anna, imap):
    """Der angeschriebene Alias war einmal ein Merkmal — aus dem Nachlauf trennt er nichts:
    an einen Catch-all geht ohnehin alles, und nach 1755 Ham-Beobachtungen zog er jede
    Beurteilung nach unten. Gelernt wird nur noch, WER geschrieben hat."""
    imap[("privat", "INBOX")] = [
        _treffer(3, "shop@laden.de", "Ihre Bestellung", to="shop-alias@alias.example")]
    await spam_bootstrap.nachlernen(db, anna.id, "privat", "INBOX", ist_spam=False)
    assert await _zaehler(db, "to:shop-alias@alias.example") == (0, 0)
    assert await _zaehler(db, "from:shop@laden.de") == (0, 1)


async def test_technische_signale_bleiben_aussen_vor(db, anna, imap):
    """Der Nachlauf sieht keine Prüfergebnisse — daraus dürfen keine `sig:`-Merkmale werden,
    sonst lernte das Gedächtnis die Leseweise statt die Mail."""
    imap[("privat", "INBOX")] = [_treffer(4, "wer@laden.de", "Rechnung 4711")]
    await spam_bootstrap.nachlernen(db, anna.id, "privat", "INBOX", ist_spam=False)
    sig = (await db.execute(select(SpamFeatureStat).where(
        SpamFeatureStat.feature.like("sig:%")))).scalars().all()
    assert sig == []


async def test_kaltstart_deckt_spam_und_ham_ab(db, anna, imap):
    imap[("privat", "Junk")] = [_treffer(1, "wer@zufall.top", "Gewinn")]
    imap[("privat", "INBOX")] = [_treffer(2, "chef@firma.de", "Termin Montag")]
    imap[("privat", "Archives/2025")] = [_treffer(3, "oma@familie.de", "Grüße")]

    bilanz = await spam_bootstrap.kaltstart(db, anna.id)
    assert bilanz == {"spam": 1, "ham": 2}
    assert await _zaehler(db, "from:chef@firma.de") == (0, 1)
    assert await _zaehler(db, "from:oma@familie.de") == (0, 1)

    # Und das Gelernte wirkt sofort: der Chef ist damit geklärt genug für einen Freispruch.
    score, gruende, _ = await spam_learn.bewerten(db, anna.id, ["from:chef@firma.de"])
    assert score < 0.5 and gruende


async def test_rueckkopplung_liest_nur_den_spam_ordner(db, anna, imap):
    imap[("privat", "Junk")] = [_treffer(9, "neu@zufall.top", "Werbung")]
    imap[("privat", "INBOX")] = [_treffer(10, "chef@firma.de", "Termin")]
    assert await spam_bootstrap.spam_rueckkopplung(db, anna.id) == 1
    assert await _zaehler(db, "from:neu@zufall.top") == (1, 0)
    assert await _zaehler(db, "from:chef@firma.de") == (0, 0)


def test_ordnerauswahl_trennt_lehrstoff_von_eigenem():
    """Gesendetes ist kein Beleg für „erwünscht" (dort bin ich der Absender), und Archive
    von vor Jahren tragen Adressen, die nie wieder auftauchen."""
    waehle = lambda n: spam_bootstrap.ist_ham_ordner(  # noqa: E731
        n, spam_folder="Junk", jetzt_jahr=2026)
    assert waehle("INBOX") and waehle("INBOX/Bewerbung") and waehle("Archives/2025")
    assert not waehle("Sent") and not waehle("Drafts") and not waehle("Junk")
    assert not waehle("Archives/2009")


async def test_kaltstart_ueberspringt_gesendetes_und_alte_archive(db, anna, imap):
    imap[("privat", "INBOX")] = [_treffer(1, "chef@firma.de", "Termin")]
    imap[("privat", "INBOX/Bewerbung")] = [_treffer(2, "hr@firma.de", "Ihre Bewerbung")]
    imap[("privat", "Archives/2025")] = [_treffer(3, "oma@familie.de", "Grüße")]
    imap[("privat", "Archives/2009")] = [_treffer(4, "alt@tot.example", "Uralt")]
    imap[("privat", "Sent")] = [_treffer(5, "ich@meine-domain.de", "Meine Antwort")]

    bilanz = await spam_bootstrap.kaltstart(db, anna.id)
    assert bilanz["ham"] == 3
    assert await _zaehler(db, "from:alt@tot.example") == (0, 0)
    assert await _zaehler(db, "from:ich@meine-domain.de") == (0, 0)
    assert await _zaehler(db, "from:hr@firma.de") == (0, 1)


# ── „Je geantwortet?" ────────────────────────────────────────────────────────

async def test_empfaenger_eigener_post_werden_bekannt(db, anna, imap):
    """Wem ich schreibe, den kenne ich — das ist der Freispruch, der keine Frage kostet."""
    from app.models.assistant import AssistantContact
    from app.services.vault_contacts import kontakt_treffer

    imap[("privat", "Sent")] = [{
        "uid": 5, "from": [{"name": "", "addr": "ich@meine-domain.de"}],
        "to": [{"name": "Rainer Beispiel", "addr": "r.beispiel@t-online.de"}],
        "cc": [{"name": "", "addr": "kasse@verein.de"}],
        "subject": "Re: Termin", "message_id": "<5@x>", "date": "", "flags": []}]

    assert await spam_bootstrap.antwort_kontakte(db, anna.id) == 2
    assert await kontakt_treffer(db, anna.id, "r.beispiel@t-online.de", "") == "sent"
    # Die eigene Adresse gehört nicht in die Liste.
    eigen = (await db.execute(select(AssistantContact).where(
        AssistantContact.email == "ich@meine-domain.de"))).scalars().all()
    assert eigen == []


async def test_gesendet_zaehlt_nur_neue_uids(db, anna, imap):
    imap[("privat", "Sent")] = [{
        "uid": 5, "from": [], "to": [{"name": "", "addr": "wer@firma.de"}], "cc": [],
        "subject": "Hallo", "message_id": "<5@x>", "date": "", "flags": []}]
    assert await spam_bootstrap.antwort_kontakte(db, anna.id) == 1
    assert await spam_bootstrap.antwort_kontakte(db, anna.id) == 0


async def test_vault_abgleich_raeumt_gesendet_kontakte_nicht_ab(db, anna, imap, tmp_path):
    """Beide Quellen teilen sich eine Tabelle — der Vault-Spiegel darf nur seine eigenen
    Einträge abräumen, sonst wäre die Antwort-Liste nach einer Stunde wieder leer."""
    from app.models.assistant import AssistantContact
    from app.services.vault_contacts import sync_contacts

    imap[("privat", "Sent")] = [{
        "uid": 1, "from": [], "to": [{"name": "", "addr": "partner@firma.de"}], "cc": [],
        "subject": "Angebot", "message_id": "<1@x>", "date": "", "flags": []}]
    await spam_bootstrap.antwort_kontakte(db, anna.id)

    ordner = tmp_path / "03 Bereiche" / "Personen"
    ordner.mkdir(parents=True)
    (ordner / "Jemand.md").write_text("---\nemail: jemand@anders.de\n---\n", encoding="utf-8")
    await sync_contacts(db, anna.id, vault_root=str(tmp_path))

    bestand = {c.email: c.source_kind for c in (await db.execute(
        select(AssistantContact))).scalars().all()}
    assert bestand == {"partner@firma.de": "sent", "jemand@anders.de": "frontmatter"}


# ── Was der Nachlauf NICHT lernen darf ───────────────────────────────────────

async def test_nachlauf_lernt_nur_die_absender_identitaet(db, anna, imap):
    """Ein Postfach enthält tausende erwünschte Mails und eine Handvoll Müll. Wer daraus
    Betreff-Wörter zählt, macht „rechnung" zum Ham-Signal — und eine Phishing-Mail mit
    genau diesem Wort rutscht danach unter die Frage-Schwelle (2026-08-18)."""
    imap[("privat", "INBOX")] = [
        _treffer(11, "buchhaltung@firma.de", "Ihre Rechnung 4711",
                 to="ich@meine-domain.de")]
    await spam_bootstrap.nachlernen(db, anna.id, "privat", "INBOX", ist_spam=False)

    assert await _zaehler(db, "from:buchhaltung@firma.de") == (0, 1)
    assert await _zaehler(db, "dom:firma.de") == (0, 1)
    # Weder Betreff-Wörter noch der angeschriebene Alias — beide sagen aus dieser Quelle
    # nichts über Spam.
    assert await _zaehler(db, "wort:rechnung") == (0, 0)
    assert await _zaehler(db, "to:ich@meine-domain.de") == (0, 0)


async def test_echte_entscheidung_lernt_weiterhin_alles(db, anna):
    """Die Beschränkung gilt nur für den Nachlauf: wo ein Mensch entschieden hat, stehen
    beide Klassen in einem Verhältnis, das etwas bedeutet."""
    from app.models.assistant import SpamVerdict

    v = SpamVerdict(owner_user_id=anna.id, sender_email="wer@zufall.top",
                    features=["from:wer@zufall.top", "wort:gewinn", "to:ich@meine-domain.de"],
                    status="spam")
    db.add(v)
    await db.flush()
    await spam_learn.merken(db, v, True)
    await db.commit()

    assert await _zaehler(db, "wort:gewinn") == (1, 0)
    assert await _zaehler(db, "to:ich@meine-domain.de") == (1, 0)
