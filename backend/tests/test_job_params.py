"""Platzhalter und Vorlagen für Jobs.

Anlass: der KI-&-Tech-News-Job trug Thema, Quellen und Aufbau fest im Prompt. Ein zweiter
Digest wäre eine Kopie gewesen. Jetzt: Vorlage + Parameter (`jobs.args` als Objekt).
"""
import datetime as dt

import pytest
from app.services.job_params import TZ, offene_platzhalter, rendere
from app.services.job_templates import JOB_TEMPLATES, anwenden, liste


def test_parameter_werden_eingesetzt():
    assert rendere("Thema: {{thema}}", {"thema": "Funk"}) == "Thema: Funk"


def test_liste_wird_zur_aufzaehlung():
    assert rendere("{{quellen}}", {"quellen": ["a", "b"]}) == "a, b"


def test_unbekannter_platzhalter_bleibt_stehen():
    """Stilles Leeren würde eine Vorgabe lautlos aus dem Auftrag entfernen — sichtbar
    falsch ist besser als unsichtbar falsch."""
    assert rendere("Quellen: {{quellen}}", {"thema": "x"}) == "Quellen: {{quellen}}"
    assert offene_platzhalter("Quellen: {{quellen}}", {"thema": "x"}) == ["quellen"]


def test_script_argumente_bleiben_unangetastet():
    """`args` ist historisch die Argumentliste der script-Jobs. Nur ein Objekt ist ein
    Parametersatz — eine Liste darf nichts ersetzen."""
    assert rendere("{{thema}}", ["--flag", "wert"]) == "{{thema}}"


def test_zeitfenster_kommt_aus_dem_letzten_lauf():
    jetzt = dt.datetime(2026, 7, 29, 8, 0, tzinfo=dt.timezone.utc)
    letzter = dt.datetime(2026, 7, 28, 8, 0, tzinfo=dt.timezone.utc)
    text = rendere("{{zeitfenster}} · {{heute}} · {{seit}}", {}, jetzt=jetzt, letzter_lauf=letzter)
    assert "2026-07-28 10:00 bis 2026-07-29 10:00" in text   # Europe/Berlin (UTC+2)
    assert "2026-07-29" in text and TZ.key == "Europe/Berlin"


def test_ohne_letzten_lauf_24_stunden_zurueck():
    """Erster Lauf oder Job war aus: ein Digest braucht trotzdem eine Untergrenze."""
    jetzt = dt.datetime(2026, 7, 29, 6, 0, tzinfo=dt.timezone.utc)
    assert "2026-07-28 08:00 bis 2026-07-29 08:00" in rendere("{{zeitfenster}}", {}, jetzt=jetzt)


def test_eigener_parameter_schlaegt_eingebauten():
    assert rendere("{{heute}}", {"heute": "Sankt Nimmerlein"}) == "Sankt Nimmerlein"


def test_vorlage_liefert_felder_und_parameter():
    felder = anwenden("recherche-digest", {"titel": "Security-News"})
    assert felder["kind"] == "prompt" and felder["result_html"] is True
    assert felder["args"]["titel"] == "Security-News"
    # Nicht überschriebene Vorgaben bleiben erhalten.
    assert felder["args"]["sprache"] == "Deutsch" and felder["args"]["quellen"]


def test_vorlage_rendert_ohne_offene_platzhalter():
    """Eine Vorlage, die out of the box Lücken lässt, wäre eine Falle."""
    felder = anwenden("recherche-digest")
    assert offene_platzhalter(felder["prompt"], felder["args"]) == []
    text = rendere(felder["prompt"], felder["args"])
    assert "{{" not in text and "Hacker News" in text


def test_unbekannte_vorlage():
    with pytest.raises(KeyError):
        anwenden("gibtsnicht")


def test_liste_zeigt_parameter():
    eintraege = {v["key"]: v for v in liste()}
    assert set(eintraege) == set(JOB_TEMPLATES)
    assert "quellen" in eintraege["recherche-digest"]["params"]
