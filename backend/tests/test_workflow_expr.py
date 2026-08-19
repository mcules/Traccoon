"""Expressions in templates: making the form the target system wants out of data.

The core of these tests is not "computes correctly" but: a crooked template must never
topple a run, and everything that worked before keeps working.
"""
import datetime as dt

import pytest
from app.services.workflow_expr import auswerten, fuellen, katalog

pytestmark = pytest.mark.asyncio

CTX = {
    "mail": {"subject": "Ihre Domain-Rechnung wartet auf Bearbeitung", "from": ""},
    "spam": {"score": 0.9123, "aktiv": True},
    "tool": {"json": {"items": [{"name": "eins"}, {"name": "zwei"}]}},
    "leer": {"wert": None},
}


async def test_reiner_pfad_verhaelt_sich_wie_frueher():
    """All existing templates stay valid; otherwise this would be a break, not an extension."""
    assert fuellen("{{ mail.subject }}", CTX) == CTX["mail"]["subject"]
    assert fuellen("Betreff: {{mail.subject}}!", CTX).startswith("Betreff: Ihre")
    # An unknown field stays empty instead of writing "None".
    assert fuellen("[{{ gibts.nicht }}]", CTX) == "[]"


async def test_filterkette_wird_von_links_nach_rechts_angewendet():
    assert fuellen("{{ spam.score | mal:100 | rund:1 }}", CTX) == "91.2"
    assert fuellen("{{ mail.subject | kurz:12 }}", CTX) == "Ihre Domain…"
    assert fuellen("{{ mail.subject | klein | kurz:11,\"\" }}", CTX) == "ihre domain"


async def test_default_greift_nur_bei_leer():
    assert fuellen('{{ mail.from | default:"unbekannt" }}', CTX) == "unbekannt"
    assert fuellen('{{ leer.wert | default:"x" }}', CTX) == "x"
    assert fuellen('{{ mail.subject | default:"x" | kurz:4,"" }}', CTX) == "Ihre"


async def test_listen_und_tiefe_pfade():
    assert fuellen("{{ tool.json.items | anzahl }}", CTX) == "2"
    assert fuellen("{{ tool.json.items.0.name }}", CTX) == "eins"
    assert fuellen('{{ tool.json.items | erstes | json }}', CTX) == '{"name": "eins"}'


async def test_zeit_rechnen_und_formatieren():
    heute = dt.datetime.now(tz=dt.timezone.utc)
    assert fuellen('{{ jetzt | datum:"%Y" }}', CTX) == str(heute.year)
    # Two hours later is another point in time: what is checked is the shift itself.
    vorher = auswerten("jetzt", CTX)
    nachher = auswerten('jetzt | plus_zeit:2,"h"', CTX)
    assert dt.datetime.fromisoformat(nachher) - dt.datetime.fromisoformat(vorher) \
        >= dt.timedelta(hours=1, minutes=59)
    # An ISO timestamp from the context can be formatted just as well.
    assert fuellen('{{ zeit | datum:"%d.%m.%Y" }}',
                   {"zeit": "2026-08-18T06:31:32+00:00"}) == "18.08.2026"


async def test_schiefe_vorlage_kippt_keinen_lauf():
    """A typo in the filter, a text instead of a number: that may at most give an ugly result,
    never an abort in the middle of the flow."""
    assert fuellen("{{ mail.subject | gibtsnicht }}", CTX) == CTX["mail"]["subject"]
    assert fuellen("{{ mail.subject | mal:2 }}", CTX) == "0.0"
    assert fuellen("{{ }}", CTX) == ""
    assert fuellen("{{ tool.json | kurz:5 }}", CTX).endswith("…")


async def test_boolesche_werte_lesen_sich_wie_erwartet():
    assert fuellen("{{ spam.aktiv }}", CTX) == "true"


async def test_katalog_erklaert_jeden_filter():
    """The list feeds the help in the editor: a filter without an explanation is worthless there."""
    eintraege = katalog()
    assert {"kurz", "default", "datum", "anzahl", "mal"} <= {e["name"] for e in eintraege}
    assert all(e["hilfe"] for e in eintraege)


async def test_filterargument_darf_aus_dem_kontext_kommen():
    """`default:event.type` should insert the value from there, not the word.

    Before, "event.type" stood literally in the Telegram message, and in the throttle key,
    which would have made all kinds of disturbance the same case.
    """
    ctx = {"event": {"type": "deviceInactive", "attributes": {}}}
    assert fuellen("{{ event.attributes.alarm | default:event.type }}", ctx) == "deviceInactive"


async def test_zitiertes_argument_bleibt_woertlich():
    ctx = {"event": {"type": "alarm"}}
    assert fuellen('{{ fehlt | default:"event.type" }}', ctx) == "event.type"


async def test_unbekannter_pfad_bleibt_als_text_stehen():
    """A default like `default:unbekannt` behaves unchanged."""
    assert fuellen("{{ fehlt | default:unbekannt }}", {}) == "unbekannt"


async def test_zahlen_bleiben_zahlen():
    assert fuellen("{{ text | kurz:4 }}", {"text": "abcdefgh"}) == "abc…"


# --- Listen und Pfade ------------------------------------------------------------------

async def test_feld_zieht_aus_einer_objektliste():
    """Der Weg vom Suchtreffer zum Satz: ohne das bleibt eine Trefferliste unbenutzbar."""
    ctx = {"t": {"hits": [{"filename": "a/VW T5.md", "x": 1}, {"filename": "b/Corsa C.md"}]}}
    assert fuellen('{{ t.hits | feld:"filename" | verbinde:", " }}', ctx) == "a/VW T5.md, b/Corsa C.md"
    # Einzelnes Objekt: derselbe Filter, ein Wert.
    assert fuellen('{{ t | feld:"hits" | anzahl }}', ctx) == "2"


async def test_feld_ueberspringt_was_es_nicht_hat():
    ctx = {"l": [{"a": 1}, {"b": 2}, "kein Objekt"]}
    assert fuellen('{{ l | feld:"a" | verbinde:"," }}', ctx) == "1"


async def test_dateiname_macht_aus_pfaden_namen():
    ctx = {"p": ["03 Bereiche/Fahrzeuge/VW T5 Multivan.md", "Opel Corsa C.md"]}
    assert fuellen('{{ p | dateiname | verbinde:" und " }}', ctx) == "VW T5 Multivan und Opel Corsa C"
    assert fuellen("{{ p | erstes | dateiname }}", ctx) == "VW T5 Multivan"


async def test_max_beantwortet_die_frage_die_eine_weiche_nicht_stellen_kann():
    """JSONLogic kennt hier kein `some`; „bringt irgendein Tag Schnee" wird deshalb erst zu
    einer Zahl gemacht und dann verglichen."""
    ctx = {"w": {"schnee": [0, 0, 1.4, 0.2], "leer": [], "text": ["0,5", "2"]}}
    assert fuellen("{{ w.schnee | max }}", ctx) == "1.4"
    assert fuellen("{{ w.schnee | min }}", ctx) == "0.0"
    assert fuellen("{{ w.leer | max }}", ctx) == "0", "nichts zu vergleichen ist kein Fehler"
    assert fuellen("{{ w.leer | min }}", ctx) == "0"
    # Zahlen als Text (JSON-APIs liefern das gern) zählen mit.
    assert fuellen("{{ w.text | max }}", ctx) == "2.0"


async def test_neue_filter_stehen_im_katalog():
    """Was der Editor nicht anbietet, findet niemand."""
    namen = {e["name"] for e in katalog()}
    assert {"feld", "dateiname", "max", "min"} <= namen
