"""Ausdrücke in Vorlagen: aus Daten die Form machen, die das Zielsystem will.

Der Kern dieser Tests ist nicht „rechnet richtig", sondern: eine schiefe Vorlage darf nie
einen Lauf kippen, und alles, was vorher ging, geht weiter.
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
    """Alle bestehenden Vorlagen bleiben gültig — sonst wäre das ein Bruch, kein Ausbau."""
    assert fuellen("{{ mail.subject }}", CTX) == CTX["mail"]["subject"]
    assert fuellen("Betreff: {{mail.subject}}!", CTX).startswith("Betreff: Ihre")
    # Unbekanntes Feld bleibt leer statt „None" zu schreiben.
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
    # Zwei Stunden später ist ein anderer Zeitpunkt — geprüft wird die Verschiebung selbst.
    vorher = auswerten("jetzt", CTX)
    nachher = auswerten('jetzt | plus_zeit:2,"h"', CTX)
    assert dt.datetime.fromisoformat(nachher) - dt.datetime.fromisoformat(vorher) \
        >= dt.timedelta(hours=1, minutes=59)
    # Ein ISO-Zeitstempel aus dem Kontext lässt sich genauso formatieren.
    assert fuellen('{{ zeit | datum:"%d.%m.%Y" }}',
                   {"zeit": "2026-08-18T06:31:32+00:00"}) == "18.08.2026"


async def test_schiefe_vorlage_kippt_keinen_lauf():
    """Ein Tippfehler im Filter, ein Text statt einer Zahl — das darf höchstens ein
    unschönes Ergebnis geben, niemals einen Abbruch mitten im Ablauf."""
    assert fuellen("{{ mail.subject | gibtsnicht }}", CTX) == CTX["mail"]["subject"]
    assert fuellen("{{ mail.subject | mal:2 }}", CTX) == "0.0"
    assert fuellen("{{ }}", CTX) == ""
    assert fuellen("{{ tool.json | kurz:5 }}", CTX).endswith("…")


async def test_boolesche_werte_lesen_sich_wie_erwartet():
    assert fuellen("{{ spam.aktiv }}", CTX) == "true"


async def test_katalog_erklaert_jeden_filter():
    """Die Liste speist die Hilfe im Editor — ein Filter ohne Erklärung ist dort wertlos."""
    eintraege = katalog()
    assert {"kurz", "default", "datum", "anzahl", "mal"} <= {e["name"] for e in eintraege}
    assert all(e["hilfe"] for e in eintraege)
