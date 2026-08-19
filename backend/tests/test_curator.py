"""The curator tidies the memory up, and has to be careful doing so.

It touches other people's memories. That is why the tests guard above all the emergency
brakes: what happens when the model is overeager, misses the format or the archive jams.
"""
import datetime as dt

import pytest
from app.services.appsettings import get_setting, set_setting
from app.worker import curator
from app.worker.curator import PIN, _teile, faellig, kuratiere_notiz
from conftest import make_user

# Long enough for tidying up to be worth it at all (MINDEST_ZEICHEN), and this is what real
# memory notes look like: one sentence per line, not two words.
NOTIZ = "\n".join(f"- Erkenntnis {i}: Der Mensch möchte das dauerhaft so gehandhabt wissen."
                  for i in range(40))
PFAD = "KI/Gedaechtnis/Mensch.md"


class FakeMcp:
    """Vault replacement: remembers what was written and what was appended."""

    def __init__(self, inhalt: str = NOTIZ, schreibfehler: str | None = None):
        self.notizen = {PFAD: inhalt}
        self.angehaengt: dict[str, str] = {}
        self.schreibfehler = schreibfehler

    async def call(self, tool: str, args: dict):
        pfad = (args.get("target") or {}).get("path", "")
        if tool == "obsidian__obsidian_get_note":
            return self.notizen.get(pfad, "")
        if tool == "obsidian__obsidian_append_to_note":
            if self.schreibfehler == "archiv":
                raise RuntimeError("Archive not writable")
            self.angehaengt[pfad] = self.angehaengt.get(pfad, "") + args["content"]
            return "ok"
        if tool == "obsidian__obsidian_write_note":
            self.notizen[pfad] = args["content"]
            return "ok"
        return "ok"


def _aux(monkeypatch, antwort):
    async def fake(*a, **kw):
        return antwort
    monkeypatch.setattr("app.worker.aux.aux_chat", fake)


async def _lauf(db, mcp, owner_id=1):
    return await kuratiere_notiz(db, mcp, owner_id=owner_id, pfad=PFAD, agent=None,
                                 tokens={}, base_urls={})


def test_antwort_zerlegen():
    b, a = _teile("### BEHALTEN\n- eins\n- zwei\n### ARCHIV\n- alt")
    assert b == "- eins\n- zwei" and a == "- alt"
    assert _teile("### BEHALTEN\n- eins\n### ARCHIV\nkeine")[1] == ""
    assert _teile("kein Format") is None
    assert _teile("### BEHALTEN\n\n### ARCHIV\n- alles weg") is None   # nichts behalten


async def test_kurze_notiz_wird_nicht_angefasst(db, monkeypatch):
    _aux(monkeypatch, "### BEHALTEN\n- eins")
    mcp = FakeMcp("- nur eine Zeile")
    assert await _lauf(db, mcp) is None
    assert mcp.notizen[PFAD] == "- nur eine Zeile"


async def test_aufraeumen_kuerzt_und_archiviert(db, monkeypatch):
    behalten = "\n".join(f"- Erkenntnis {i}: zusammengefasst." for i in range(20))
    _aux(monkeypatch, f"### BEHALTEN\n{behalten}\n### ARCHIV\n- Erkenntnis 39: alt.")
    mcp = FakeMcp()
    bericht = await _lauf(db, mcp)
    assert bericht and "40 → 20" in bericht
    assert "Erkenntnis 0" in mcp.notizen[PFAD] and "Erkenntnis 39" not in mcp.notizen[PFAD]
    # What is sorted out is not gone but lies in the archive beside it.
    archiv = mcp.angehaengt["KI/Gedaechtnis/Archiv-Mensch.md"]
    assert "Erkenntnis 39" in archiv and "Aussortiert am" in archiv


async def test_angepinntes_muss_ueberleben(db, monkeypatch):
    """If a pin is missing in the result, nothing is written at all: the human nailed this
    line down explicitly."""
    inhalt = NOTIZ + f"\n- {PIN} Niemals ohne Rückfrage deployen"
    _aux(monkeypatch, "### BEHALTEN\n" + "\n".join(
        f"- Erkenntnis {i}: zusammengefasst." for i in range(20)))
    mcp = FakeMcp(inhalt)
    assert await _lauf(db, mcp) is None
    assert mcp.notizen[PFAD] == inhalt


async def test_kahlschlag_wird_verweigert(db, monkeypatch):
    """Zwei Drittel weg ist kein Aufräumen mehr."""
    _aux(monkeypatch, "### BEHALTEN\n- eins\n### ARCHIV\n- der ganze Rest")
    mcp = FakeMcp()
    assert await _lauf(db, mcp) is None
    assert mcp.notizen[PFAD] == NOTIZ


async def test_formatfehler_laesst_alles_stehen(db, monkeypatch):
    _aux(monkeypatch, "Klar, ich habe aufgeräumt! Hier das Ergebnis: ...")
    mcp = FakeMcp()
    assert await _lauf(db, mcp) is None
    assert mcp.notizen[PFAD] == NOTIZ


async def test_ohne_aux_passiert_nichts(db, monkeypatch):
    _aux(monkeypatch, None)
    mcp = FakeMcp()
    assert await _lauf(db, mcp) is None
    assert mcp.notizen[PFAD] == NOTIZ


async def test_klemmendes_archiv_haelt_die_notiz_unveraendert(db, monkeypatch):
    """Archive first, truncate afterwards: if the archive jams, nothing may be truncated,
    because otherwise what was sorted out would be gone without replacement."""
    behalten = "\n".join(f"- Erkenntnis {i}: zusammengefasst." for i in range(20))
    _aux(monkeypatch, f"### BEHALTEN\n{behalten}\n### ARCHIV\n- Erkenntnis 39: alt.")
    mcp = FakeMcp(schreibfehler="archiv")
    assert await _lauf(db, mcp) is None
    assert mcp.notizen[PFAD] == NOTIZ


async def test_hoechstens_einmal_am_tag(db, monkeypatch):
    jetzt = dt.datetime.now(tz=dt.timezone.utc)
    assert await faellig(db, 1, PFAD) is True                     # never run yet
    await set_setting(db, f"curator_last:1:{PFAD}", jetzt.isoformat())
    assert await faellig(db, 1, PFAD, jetzt=jetzt) is False
    assert await faellig(db, 1, PFAD, jetzt=jetzt + dt.timedelta(hours=25)) is True
    # An unreadable timestamp must not block permanently.
    await set_setting(db, f"curator_last:1:{PFAD}", "kaputt")
    assert await faellig(db, 1, PFAD) is True


async def test_erfolgreicher_lauf_merkt_sich_den_zeitpunkt(db, monkeypatch):
    behalten = "\n".join(f"- Erkenntnis {i}: zusammengefasst." for i in range(20))
    _aux(monkeypatch, f"### BEHALTEN\n{behalten}\n### ARCHIV\nkeine")
    await _lauf(db, FakeMcp(), owner_id=7)
    assert await get_setting(db, f"curator_last:7:{PFAD}", "") != ""
    assert await faellig(db, 7, PFAD) is False


async def test_ohne_eigenes_modell_bleibt_die_pflege_aus(db, monkeypatch):
    """The curator is diligence work in the background. If it ran on the working model for
    lack of a setting, it would cost money unasked AND write in the vault of the human."""
    from app.worker import __main__ as worker

    gelaufen = {}

    async def fake_kuratiere(*a, **kw):
        gelaufen["ja"] = True
        return []

    monkeypatch.setattr("app.worker.curator.kuratiere", fake_kuratiere)
    await worker._handle_curator({"owner_id": 1, "agent_role": "assistent"})
    assert not gelaufen
