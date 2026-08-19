"""Truncating the context without breaking the run.

`max_context_tokens` was a dead field: set, never read. Now it is measured and summarised.
The tests guard above all the two ways such a thing goes wrong: cutting in the wrong place
(the provider refuses) or truncating the assignment away.
"""
import pytest
from app.worker import compaction
from app.worker.compaction import kompaktiere, plan


def _lauf(n: int) -> list[dict]:
    """System + Auftrag + n Wortwechsel."""
    m = [{"role": "system", "content": "Du bist ein Agent."},
         {"role": "user", "content": "Der Auftrag."}]
    for i in range(n):
        m.append({"role": "assistant", "content": f"Schritt {i}"})
        m.append({"role": "user", "content": f"Weiter {i}"})
    return m


def test_unter_der_schwelle_passiert_nichts():
    assert plan(_lauf(20), grenze_tokens=100_000, gemessen=50_000) is None


def test_ohne_grenze_passiert_nichts():
    """No `max_context_tokens` means the behaviour as before, no matter how large the context."""
    assert plan(_lauf(20), grenze_tokens=0, gemessen=10_000_000) is None


def test_ueber_der_schwelle_wird_der_mittelteil_gewaehlt():
    m = _lauf(20)
    von, bis = plan(m, grenze_tokens=100_000, gemessen=85_000)
    assert von == 2                      # system plus assignment stay untouched
    assert bis <= len(m) - compaction.BEHALTEN + 1
    assert bis - von >= compaction.MINDEST_BLOCK


def test_kurzer_verlauf_lohnt_nicht():
    assert plan(_lauf(1), grenze_tokens=1000, gemessen=999) is None


def test_schnitt_trennt_nie_werkzeugaufruf_von_seiner_antwort():
    """The expensive error: an `assistant` with tool_calls without the corresponding `tool`
    answers is an invalid request for the provider (HTTP 400), turning threatening into certain."""
    m = [{"role": "system", "content": "sys"}, {"role": "user", "content": "Auftrag"}]
    for i in range(8):
        m.append({"role": "user", "content": f"frag {i}"})
        m.append({"role": "assistant", "content": "", "tool_calls": [
            {"id": f"c{i}", "function": {"name": "lies", "arguments": "{}"}}]})
        m.append({"role": "tool", "tool_call_id": f"c{i}", "name": "lies", "content": "Ergebnis"})
    von, bis = plan(m, grenze_tokens=1000, gemessen=900)
    assert m[bis]["role"] != "tool"          # never cut before a tool answer
    assert "tool_call_id" not in m[bis]
    # And the rest stays a valid interplay: no `tool` without its `assistant`.
    rest = m[bis:]
    for i, nachricht in enumerate(rest):
        if nachricht.get("role") == "tool":
            assert rest[i - 1].get("tool_calls"), "a tool answer without its call"


async def test_zusammenfassung_ersetzt_den_mittelteil(db, monkeypatch):
    async def fake_aux(*a, **kw):
        return "- Schritt A erledigt\n- Entscheidung B getroffen"

    monkeypatch.setattr("app.worker.aux.aux_chat", fake_aux)
    m = _lauf(20)
    neu = await kompaktiere(db, messages=m, grenze_tokens=100_000, gemessen=90_000,
                            owner_id=1, agent=None, tokens={}, base_urls={})
    assert neu is not None and len(neu) < len(m)
    assert neu[0] == m[0] and neu[1] == m[1]              # system plus assignment unchanged
    assert "Entscheidung B" in neu[2]["content"]
    assert neu[-1] == m[-1]                                # the most recent stays verbatim


async def test_ohne_aux_wird_trotzdem_gekuerzt_aber_ehrlich(db, monkeypatch):
    """An aborted run is worse than one with a gap in its memory, but the agent has to KNOW
    about the gap; otherwise it takes it for completeness."""
    async def fake_aux(*a, **kw):
        return None

    monkeypatch.setattr("app.worker.aux.aux_chat", fake_aux)
    m = _lauf(20)
    neu = await kompaktiere(db, messages=m, grenze_tokens=100_000, gemessen=90_000,
                            owner_id=1, agent=None, tokens={}, base_urls={})
    assert neu is not None and len(neu) < len(m)
    assert "nicht möglich" in neu[2]["content"] and "verloren" in neu[2]["content"]


async def test_nichts_zu_tun_gibt_none(db):
    assert await kompaktiere(db, messages=_lauf(3), grenze_tokens=100_000, gemessen=10,
                             owner_id=1, agent=None, tokens={}, base_urls={}) is None


def test_anthropic_blockformat_wird_lesbar_zusammengefuehrt():
    """Anthropic delivers content as a list of blocks, and the template for the aux model has
    to make text of it regardless; otherwise it summarises empty messages."""
    text = compaction._als_text([
        {"role": "assistant", "content": [{"type": "text", "text": "Ich prüfe das."}]},
        {"role": "assistant", "content": "", "tool_calls": [
            {"id": "c1", "function": {"name": "datei_lesen", "arguments": "{}"}}]},
    ])
    assert "Ich prüfe das." in text and "datei_lesen" in text


async def test_grosser_verlauf_wird_haeppchenweise_gefasst(db, monkeypatch):
    """The aux model is deliberately small (local, 32k). If it gets the whole history of a
    200k model it refuses, and the agent would stand there without a summary. So: cut into
    pieces and catch them piece by piece, each of them small enough on its own."""
    gesehen = {"laengen": []}

    async def fake_aux(*a, **kw):
        gesehen["laengen"].append(len(kw["messages"][0]["content"]))
        gesehen["laenge"] = gesehen["laengen"][-1]
        return "- gefasst"

    monkeypatch.setattr("app.worker.aux.aux_chat", fake_aux)
    m = [{"role": "system", "content": "sys"}, {"role": "user", "content": "Auftrag"}]
    for i in range(200):
        m.append({"role": "assistant", "content": f"Schritt {i} " + "x" * 1500})
        m.append({"role": "user", "content": f"Weiter {i}"})
    neu = await kompaktiere(db, messages=m, grenze_tokens=100_000, gemessen=90_000,
                            owner_id=1, agent=None, tokens={}, base_urls={})
    assert neu is not None
    # Not a single assignment blows the aux model …
    assert gesehen["laengen"] and max(gesehen["laengen"]) <= (
        compaction.MAX_AUX_ZEICHEN + len(compaction.AUFTRAG) + 40)
    # … and there were several, instead of trying everything at once.
    assert len(gesehen["laengen"]) > 1
    assert "gefasst" in neu[2]["content"]
    # The most recent stays verbatim: the agent does not lose its working thread.
    assert len(neu) > compaction.BEHALTEN


async def test_reiner_werkzeugverlauf_behaelt_kopf_und_juengstes(db, monkeypatch):
    """The ABC-4 case: 60 rounds of nothing but tool calls, without a single user or system
    message in between. Formerly the truncation only knew "everything" here: the history down
    to three messages, the agent without memory, starting from the front and writing not a line of code in two runs."""
    async def fake_aux(*a, **kw):
        return "- Dateien X und Y gelesen"

    monkeypatch.setattr("app.worker.aux.aux_chat", fake_aux)
    m = [{"role": "system", "content": "sys"}, {"role": "user", "content": "Auftrag"}]
    for i in range(40):
        m.append({"role": "assistant", "content": "", "tool_calls": [
            {"id": f"c{i}", "function": {"name": "fs_read", "arguments": "{}"}}]})
        m.append({"role": "tool", "tool_call_id": f"c{i}", "name": "fs_read",
                  "content": "Dateiinhalt " * 200})

    neu = await kompaktiere(db, messages=m, grenze_tokens=100_000, gemessen=90_000,
                            owner_id=1, agent=None, tokens={}, base_urls={})

    assert neu is not None
    assert neu[0] == m[0] and neu[1] == m[1]                  # the assignment survives
    assert "Dateien X und Y" in neu[2]["content"]             # echte Zusammenfassung
    # And explicitly NOT only the head plus the summary: the most recent working context stays.
    assert len(neu) >= 3 + compaction.BEHALTEN - 1
    assert neu[-1] == m[-1]
    # The rest has to stay a valid interplay; otherwise the provider answers with a 400.
    for i, nachricht in enumerate(neu):
        if nachricht.get("role") == "tool":
            assert neu[i - 1].get("tool_calls"), "a tool answer without its call"


async def test_uebergabe_traegt_den_faden_weiter(db, monkeypatch):
    """The continuation gets insights, what is done and the next step, not the last sentence.
    ABC-12 began three runs in a row with the same search query on 2026-08-07, because the
    handover consisted of "time limit reached … (no text)"."""
    gesehen = []

    async def fake_aux(*a, **kw):
        gesehen.append(kw["messages"][0]["content"])
        return ("**Erkenntnisse** fleets.ts trägt dispatchExpedition\n"
                "**Erledigt** nichts geändert\n**Nächster Schritt** computeSammeln anlegen")

    monkeypatch.setattr("app.worker.aux.aux_chat", fake_aux)
    m = [{"role": "system", "content": "sys"}, {"role": "user", "content": "Auftrag"}]
    for i in range(30):
        m.append({"role": "assistant", "content": f"Ich lese Datei {i}"})
        m.append({"role": "user", "content": f"Ergebnis {i}"})

    text = await compaction.uebergabe(
        db, messages=m, grund="Zeitlimit erreicht (1800s).", letzter_text="",
        owner_id=1, agent=None, tokens={}, base_urls={})

    assert text.startswith("Zeitlimit erreicht")          # WELCHE Grenze, bleibt vorn
    assert "Nächster Schritt" in text and "computeSammeln" in text
    assert any("Übergabe" in g or "Erkenntnisse" in g for g in gesehen)


async def test_uebergabe_faellt_ehrlich_zurueck(db, monkeypatch):
    """Without an aux model, better the old, meagre stopgap than nothing at all."""
    async def fake_aux(*a, **kw):
        return None

    monkeypatch.setattr("app.worker.aux.aux_chat", fake_aux)
    m = [{"role": "system", "content": "sys"}, {"role": "user", "content": "Auftrag"}]
    for i in range(10):
        m.append({"role": "assistant", "content": f"Schritt {i}"})
        m.append({"role": "user", "content": f"Weiter {i}"})

    text = await compaction.uebergabe(
        db, messages=m, grund="Iterations-Limit erreicht.", letzter_text="war gerade bei X",
        owner_id=1, agent=None, tokens={}, base_urls={})
    assert "Iterations-Limit erreicht." in text
    assert "war gerade bei X" in text or "nicht möglich" in text


async def test_uebergabe_bei_kurzem_lauf_bleibt_schlicht(db, monkeypatch):
    """A run with two turns needs no aux round: the stopgap already says everything."""
    async def fake_aux(*a, **kw):
        raise AssertionError("aux must not even be asked here")

    monkeypatch.setattr("app.worker.aux.aux_chat", fake_aux)
    m = [{"role": "system", "content": "sys"}, {"role": "user", "content": "Auftrag"},
         {"role": "assistant", "content": "einmal geschaut"}]
    text = await compaction.uebergabe(
        db, messages=m, grund="Zeitlimit erreicht.", letzter_text="einmal geschaut",
        owner_id=1, agent=None, tokens={}, base_urls={})
    assert text == "Zeitlimit erreicht.\n\nLetzter Stand:\neinmal geschaut"
