"""Kontext kürzen, ohne den Lauf kaputtzumachen.

`max_context_tokens` war ein totes Feld — gesetzt, nie gelesen. Jetzt wird gemessen und
zusammengefasst. Die Tests bewachen vor allem die zwei Wege, auf denen so etwas schiefgeht:
an der falschen Stelle schneiden (Provider lehnt ab) oder den Auftrag wegkürzen.
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
    """Kein `max_context_tokens` → Verhalten wie vorher, egal wie groß der Kontext ist."""
    assert plan(_lauf(20), grenze_tokens=0, gemessen=10_000_000) is None


def test_ueber_der_schwelle_wird_der_mittelteil_gewaehlt():
    m = _lauf(20)
    von, bis = plan(m, grenze_tokens=100_000, gemessen=85_000)
    assert von == 2                      # system + Auftrag bleiben unangetastet
    assert bis <= len(m) - compaction.BEHALTEN + 1
    assert bis - von >= compaction.MINDEST_BLOCK


def test_kurzer_verlauf_lohnt_nicht():
    assert plan(_lauf(1), grenze_tokens=1000, gemessen=999) is None


def test_schnitt_trennt_nie_werkzeugaufruf_von_seiner_antwort():
    """Der teure Fehler: ein `assistant` mit tool_calls ohne die zugehörigen `tool`-Antworten
    ist für den Provider ein ungültiger Request (HTTP 400) — aus drohend würde sicher."""
    m = [{"role": "system", "content": "sys"}, {"role": "user", "content": "Auftrag"}]
    for i in range(8):
        m.append({"role": "user", "content": f"frag {i}"})
        m.append({"role": "assistant", "content": "", "tool_calls": [
            {"id": f"c{i}", "function": {"name": "lies", "arguments": "{}"}}]})
        m.append({"role": "tool", "tool_call_id": f"c{i}", "name": "lies", "content": "Ergebnis"})
    von, bis = plan(m, grenze_tokens=1000, gemessen=900)
    assert m[bis]["role"] in ("user", "system")
    assert "tool_call_id" not in m[bis]
    # Und der Rest bleibt ein gültiges Wechselspiel: kein `tool` ohne seinen `assistant`.
    rest = m[bis:]
    for i, nachricht in enumerate(rest):
        if nachricht.get("role") == "tool":
            assert rest[i - 1].get("tool_calls"), "tool-Antwort ohne ihren Aufruf"


async def test_zusammenfassung_ersetzt_den_mittelteil(db, monkeypatch):
    async def fake_aux(*a, **kw):
        return "- Schritt A erledigt\n- Entscheidung B getroffen"

    monkeypatch.setattr("app.worker.aux.aux_chat", fake_aux)
    m = _lauf(20)
    neu = await kompaktiere(db, messages=m, grenze_tokens=100_000, gemessen=90_000,
                            owner_id=1, agent=None, tokens={}, base_urls={})
    assert neu is not None and len(neu) < len(m)
    assert neu[0] == m[0] and neu[1] == m[1]              # System + Auftrag unverändert
    assert "Entscheidung B" in neu[2]["content"]
    assert neu[-1] == m[-1]                                # das Jüngste bleibt wörtlich


async def test_ohne_aux_wird_trotzdem_gekuerzt_aber_ehrlich(db, monkeypatch):
    """Ein abgebrochener Lauf ist schlimmer als einer mit Gedächtnislücke — aber der Agent
    muss von der Lücke WISSEN, sonst hält er sie für Vollständigkeit."""
    async def fake_aux(*a, **kw):
        return None

    monkeypatch.setattr("app.worker.aux.aux_chat", fake_aux)
    m = _lauf(20)
    neu = await kompaktiere(db, messages=m, grenze_tokens=100_000, gemessen=90_000,
                            owner_id=1, agent=None, tokens={}, base_urls={})
    assert neu is not None and len(neu) < len(m)
    assert "NICHT mehr bekannt" in neu[2]["content"]


async def test_nichts_zu_tun_gibt_none(db):
    assert await kompaktiere(db, messages=_lauf(3), grenze_tokens=100_000, gemessen=10,
                             owner_id=1, agent=None, tokens={}, base_urls={}) is None


def test_anthropic_blockformat_wird_lesbar_zusammengefuehrt():
    """Anthropic liefert Inhalte als Blockliste — die Vorlage fürs Aux-Modell muss daraus
    trotzdem Text machen, sonst fasst es leere Nachrichten zusammen."""
    text = compaction._als_text([
        {"role": "assistant", "content": [{"type": "text", "text": "Ich prüfe das."}]},
        {"role": "assistant", "content": "", "tool_calls": [
            {"id": "c1", "function": {"name": "datei_lesen", "arguments": "{}"}}]},
    ])
    assert "Ich prüfe das." in text and "datei_lesen" in text


async def test_grosser_verlauf_wird_haeppchenweise_gefasst(db, monkeypatch):
    """Das Aux-Modell ist bewusst klein (lokal, 32k). Bekommt es den ganzen Verlauf eines
    200k-Modells, weist es ab — und die Kompaktierung liefe immer in den harten Schnitt."""
    gesehen = {}

    async def fake_aux(*a, **kw):
        gesehen["laenge"] = len(kw["messages"][0]["content"])
        return "- gefasst"

    monkeypatch.setattr("app.worker.aux.aux_chat", fake_aux)
    m = [{"role": "system", "content": "sys"}, {"role": "user", "content": "Auftrag"}]
    for i in range(200):
        m.append({"role": "assistant", "content": f"Schritt {i} " + "x" * 1500})
        m.append({"role": "user", "content": f"Weiter {i}"})
    neu = await kompaktiere(db, messages=m, grenze_tokens=100_000, gemessen=90_000,
                            owner_id=1, agent=None, tokens={}, base_urls={})
    assert neu is not None
    assert gesehen["laenge"] <= compaction.MAX_AUX_ZEICHEN + len(compaction.AUFTRAG)
    # Was nicht mehr hineinpasste, steht weiterhin wörtlich da (und kommt beim nächsten Mal).
    assert len(neu) > compaction.BEHALTEN
