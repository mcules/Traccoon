"""Learning material from the mailboxes: what is already decided need not be asked about.

What is checked is the mechanics that easily goes wrong there: that no pass counts twice,
that only load bearing features are learned, and that foreign recipients from bulk sending do
not wander into the memory.
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


def _hits(uid: int, addr: str, subject: str, to: str = "ich@meine-domain.de") -> dict:
    return {"uid": uid, "from": [{"name": "", "addr": addr}],
            "to": [{"name": "", "addr": to}], "cc": [], "subject": subject,
            "message_id": f"<{uid}@x>", "date": "", "flags": []}


@pytest.fixture
def imap(monkeypatch):
    """Replace `imap-mcp` by a folder stock: {(account, folder): [hit, …]}."""
    bestand: dict[tuple[str, str], list[dict]] = {}
    konten = [{"alias": "privat", "inbox_folder": "INBOX", "spam_folder": "Junk"}]
    folder = {"privat": ["INBOX", "INBOX/Bewerbung", "Archives/2025", "Archives/2009",
                         "Sent", "Drafts", "Junk"]}

    async def fake_call_tool(url, tool, arguments, **kw):
        if tool == "list_accounts":
            return {"structuredContent": {"accounts": konten}}
        if tool == "list_folders":
            return {"structuredContent": {"folders": [
                {"name": n, "ignored": False} for n in folder[arguments["account"]]]}}
        if tool == "search_emails":
            hits = bestand.get((arguments["account"], arguments["folder"]), [])
            return {"structuredContent": {"results": hits[-arguments["limit"]:]}}
        raise AssertionError(f"unerwartetes Werkzeug {tool}")

    monkeypatch.setattr(spam_bootstrap, "call_tool", fake_call_tool)
    return bestand


async def _counter(db, feature: str) -> tuple[int, int]:
    row = (await db.execute(select(SpamFeatureStat).where(
        SpamFeatureStat.feature == feature))).scalars().first()
    return ((row.spam_count, row.ham_count) if row else (0, 0))


async def test_spam_folder_wird_zu_lehrstoff(db, anna, imap):
    imap[("privat", "Junk")] = [_hits(1, "wer@zufall.top", "Gewinn abholen")]
    gelesen, gelernt = await spam_bootstrap.nachlernen(
        db, anna.id, "privat", "Junk", ist_spam=True)
    assert (gelesen, gelernt) == (1, 1)
    assert await _counter(db, "from:wer@zufall.top") == (1, 0)
    assert await _counter(db, "dom:zufall.top") == (1, 0)


async def test_zweiter_lauf_zaehlt_nicht_doppelt(db, anna, imap):
    imap[("privat", "Junk")] = [_hits(1, "wer@zufall.top", "Gewinn abholen")]
    await spam_bootstrap.nachlernen(db, anna.id, "privat", "Junk", ist_spam=True)
    gelesen, gelernt = await spam_bootstrap.nachlernen(
        db, anna.id, "privat", "Junk", ist_spam=True)
    assert (gelesen, gelernt) == (1, 0)
    assert await _counter(db, "from:wer@zufall.top") == (1, 0)

    # Only what comes NEWLY in counts again.
    imap[("privat", "Junk")].append(_hits(2, "wer@zufall.top", "Noch ein Gewinn"))
    _, gelernt = await spam_bootstrap.nachlernen(db, anna.id, "privat", "Junk", ist_spam=True)
    assert gelernt == 1
    assert await _counter(db, "from:wer@zufall.top") == (2, 0)
    assert await get_setting(db, "spam_lernstand:privat:Junk") == "2"


async def test_fremde_recipient_werden_nicht_gelernt(db, anna, imap):
    """Bulk sending carries foreign addresses in To and CC, and those never turn up again."""
    imap[("privat", "Junk")] = [
        _hits(7, "wer@zufall.top", "Angebot", to="fremde.person@woanders.de")]
    await spam_bootstrap.nachlernen(db, anna.id, "privat", "Junk", ist_spam=True)
    assert await _counter(db, "to:fremde.person@woanders.de") == (0, 0)
    assert await _counter(db, "from:wer@zufall.top") == (1, 0)


async def test_alias_wird_aus_dem_nachlauf_nicht_gelernt(db, anna, imap):
    """The addressed alias was once a feature; out of the follow-up it separates nothing:
    everything goes to a catch-all anyway, and after 1755 ham observations it pulled every
    assessment down. What is learned is only WHO wrote."""
    imap[("privat", "INBOX")] = [
        _hits(3, "shop@laden.de", "Ihre Bestellung", to="shop-alias@alias.example")]
    await spam_bootstrap.nachlernen(db, anna.id, "privat", "INBOX", ist_spam=False)
    assert await _counter(db, "to:shop-alias@alias.example") == (0, 0)
    assert await _counter(db, "from:shop@laden.de") == (0, 1)


async def test_technische_signale_bleiben_aussen_vor(db, anna, imap):
    """The follow-up sees no check results, and no `sig:` features may be made of that;
    otherwise the memory would learn the way of reading instead of the mail."""
    imap[("privat", "INBOX")] = [_hits(4, "wer@laden.de", "Rechnung 4711")]
    await spam_bootstrap.nachlernen(db, anna.id, "privat", "INBOX", ist_spam=False)
    sig = (await db.execute(select(SpamFeatureStat).where(
        SpamFeatureStat.feature.like("sig:%")))).scalars().all()
    assert sig == []


async def test_kaltstart_deckt_spam_und_ham_ab(db, anna, imap):
    imap[("privat", "Junk")] = [_hits(1, "wer@zufall.top", "Gewinn")]
    imap[("privat", "INBOX")] = [_hits(2, "chef@firma.de", "Termin Montag")]
    imap[("privat", "Archives/2025")] = [_hits(3, "oma@familie.de", "Grüße")]

    bilanz = await spam_bootstrap.kaltstart(db, anna.id)
    assert bilanz == {"spam": 1, "ham": 2}
    assert await _counter(db, "from:chef@firma.de") == (0, 1)
    assert await _counter(db, "from:oma@familie.de") == (0, 1)

    # And what is learned takes effect immediately: the boss is settled enough for an acquittal.
    score, reasons, _ = await spam_learn.bewerten(db, anna.id, ["from:chef@firma.de"])
    assert score < 0.5 and reasons


async def test_rueckkopplung_liest_nur_den_spam_folder(db, anna, imap):
    imap[("privat", "Junk")] = [_hits(9, "neu@zufall.top", "Werbung")]
    imap[("privat", "INBOX")] = [_hits(10, "chef@firma.de", "Termin")]
    assert await spam_bootstrap.spam_rueckkopplung(db, anna.id) == 1
    assert await _counter(db, "from:neu@zufall.top") == (1, 0)
    assert await _counter(db, "from:chef@firma.de") == (0, 0)


def test_ordnerauswahl_trennt_lehrstoff_von_eigenem():
    """Sent mail is no proof of "wanted" (I am the sender there), and archives from years ago
    carry addresses that never turn up again."""
    waehle = lambda n: spam_bootstrap.ist_ham_folder(  # noqa: E731
        n, spam_folder="Junk", now_jahr=2026)
    assert waehle("INBOX") and waehle("INBOX/Bewerbung") and waehle("Archives/2025")
    assert not waehle("Sent") and not waehle("Drafts") and not waehle("Junk")
    assert not waehle("Archives/2009")


async def test_kaltstart_ueberspringt_gesendetes_und_alte_archive(db, anna, imap):
    imap[("privat", "INBOX")] = [_hits(1, "chef@firma.de", "Termin")]
    imap[("privat", "INBOX/Bewerbung")] = [_hits(2, "hr@firma.de", "Ihre Bewerbung")]
    imap[("privat", "Archives/2025")] = [_hits(3, "oma@familie.de", "Grüße")]
    imap[("privat", "Archives/2009")] = [_hits(4, "alt@tot.example", "Uralt")]
    imap[("privat", "Sent")] = [_hits(5, "ich@meine-domain.de", "Meine Antwort")]

    bilanz = await spam_bootstrap.kaltstart(db, anna.id)
    assert bilanz["ham"] == 3
    assert await _counter(db, "from:alt@tot.example") == (0, 0)
    assert await _counter(db, "from:ich@meine-domain.de") == (0, 0)
    assert await _counter(db, "from:hr@firma.de") == (0, 1)


# ── „Je geantwortet?" ────────────────────────────────────────────────────────

async def test_recipient_eigener_post_werden_known(db, anna, imap):
    """Whoever I write to I know; that is the acquittal that costs no question."""
    from app.models.assistant import AssistantContact
    from app.services.vault_contacts import kontakt_hits

    imap[("privat", "Sent")] = [{
        "uid": 5, "from": [{"name": "", "addr": "ich@meine-domain.de"}],
        "to": [{"name": "Rainer Beispiel", "addr": "r.beispiel@example.net"}],
        "cc": [{"name": "", "addr": "kasse@verein.de"}],
        "subject": "Re: Termin", "message_id": "<5@x>", "date": "", "flags": []}]

    assert await spam_bootstrap.answer_kontakte(db, anna.id) == 2
    assert await kontakt_hits(db, anna.id, "r.beispiel@example.net", "") == "sent"
    # One's own address does not belong in the list.
    eigen = (await db.execute(select(AssistantContact).where(
        AssistantContact.email == "ich@meine-domain.de"))).scalars().all()
    assert eigen == []


async def test_gesendet_zaehlt_nur_new_uids(db, anna, imap):
    imap[("privat", "Sent")] = [{
        "uid": 5, "from": [], "to": [{"name": "", "addr": "wer@firma.de"}], "cc": [],
        "subject": "Hallo", "message_id": "<5@x>", "date": "", "flags": []}]
    assert await spam_bootstrap.answer_kontakte(db, anna.id) == 1
    assert await spam_bootstrap.answer_kontakte(db, anna.id) == 0


async def test_vault_reconcile_raeumt_gesendet_kontakte_nicht_ab(db, anna, imap, tmp_path):
    """Both sources share one table: the vault mirror may only clear away its own entries;
    otherwise the answer list would be empty again after an hour."""
    from app.models.assistant import AssistantContact
    from app.services.vault_contacts import sync_contacts

    imap[("privat", "Sent")] = [{
        "uid": 1, "from": [], "to": [{"name": "", "addr": "partner@firma.de"}], "cc": [],
        "subject": "Angebot", "message_id": "<1@x>", "date": "", "flags": []}]
    await spam_bootstrap.answer_kontakte(db, anna.id)

    folder = tmp_path / "03 Bereiche" / "Personen"
    folder.mkdir(parents=True)
    (folder / "Jemand.md").write_text("---\nemail: jemand@anders.de\n---\n", encoding="utf-8")
    await sync_contacts(db, anna.id, vault_root=str(tmp_path))

    bestand = {c.email: c.source_kind for c in (await db.execute(
        select(AssistantContact))).scalars().all()}
    assert bestand == {"partner@firma.de": "sent", "jemand@anders.de": "frontmatter"}


# ── What the follow-up must NOT learn ────────────────────────────────────────

async def test_nachlauf_lernt_nur_die_absender_identity(db, anna, imap):
    """A mailbox contains thousands of wanted mails and a handful of rubbish. Whoever counts
    subject words from that makes "rechnung" a ham signal, and a phishing mail with exactly
    that word then slips below the question threshold (2026-08-18)."""
    imap[("privat", "INBOX")] = [
        _hits(11, "buchhaltung@firma.de", "Ihre Rechnung 4711",
                 to="ich@meine-domain.de")]
    await spam_bootstrap.nachlernen(db, anna.id, "privat", "INBOX", ist_spam=False)

    assert await _counter(db, "from:buchhaltung@firma.de") == (0, 1)
    assert await _counter(db, "dom:firma.de") == (0, 1)
    # Neither subject words nor the addressed alias: both say nothing about spam from this
    # source.
    assert await _counter(db, "wort:rechnung") == (0, 0)
    assert await _counter(db, "to:ich@meine-domain.de") == (0, 0)


async def test_echte_entscheidung_lernt_weiterhin_alles(db, anna):
    """The restriction applies only to the follow-up: where a human decided, both classes
    stand in a ratio that means something."""
    from app.models.assistant import SpamVerdict

    v = SpamVerdict(owner_user_id=anna.id, sender_email="wer@zufall.top",
                    features=["from:wer@zufall.top", "wort:gewinn", "to:ich@meine-domain.de"],
                    status="spam")
    db.add(v)
    await db.flush()
    await spam_learn.merken(db, v, True)
    await db.commit()

    assert await _counter(db, "wort:gewinn") == (1, 0)
    assert await _counter(db, "to:ich@meine-domain.de") == (1, 0)
