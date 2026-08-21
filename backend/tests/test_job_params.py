"""Placeholders and templates for jobs.

The occasion: the AI and tech news job carried topic, sources and structure firmly in the prompt. A second digest would have been a copy. Now: a template plus parameters (`jobs.args` as an object).
"""
import datetime as dt

import pytest
from app.services.job_params import STD_TZ as TZ, offene_platzhalter, rendere
from app.services.job_templates import JOB_TEMPLATES, anwenden, listing


def test_parameter_werden_eingesetzt():
    assert rendere("Thema: {{thema}}", {"thema": "Funk"}) == "Thema: Funk"


def test_listing_wird_zur_aufzaehlung():
    assert rendere("{{quellen}}", {"quellen": ["a", "b"]}) == "a, b"


def test_unbekannter_platzhalter_bleibt_stehen():
    """Emptying silently would remove a rule from the assignment without a sound; visibly
    wrong is better than invisibly wrong."""
    assert rendere("Quellen: {{quellen}}", {"thema": "x"}) == "Quellen: {{quellen}}"
    assert offene_platzhalter("Quellen: {{quellen}}", {"thema": "x"}) == ["quellen"]


def test_script_argumente_bleiben_unangetastet():
    """`args` is historically the argument list of the script jobs. Only an object is a
    parameter set; a list must replace nothing."""
    assert rendere("{{thema}}", ["--flag", "wert"]) == "{{thema}}"


def test_zeitfenster_kommt_aus_dem_letzten_lauf():
    now = dt.datetime(2026, 7, 29, 8, 0, tzinfo=dt.timezone.utc)
    last = dt.datetime(2026, 7, 28, 8, 0, tzinfo=dt.timezone.utc)
    text = rendere("{{window}} · {{today}} · {{since}}", {}, now=now, last_lauf=last)
    assert "2026-07-28 10:00 bis 2026-07-29 10:00" in text   # Europe/Berlin (UTC+2)
    assert "2026-07-29" in text and TZ.key == "Europe/Berlin"


def test_ohne_letzten_lauf_24_hours_zurueck():
    """First run or the job was off: a digest still needs a lower bound."""
    now = dt.datetime(2026, 7, 29, 6, 0, tzinfo=dt.timezone.utc)
    assert "2026-07-28 08:00 bis 2026-07-29 08:00" in rendere("{{window}}", {}, now=now)


def test_eigener_parameter_schlaegt_eingebauten():
    assert rendere("{{today}}", {"today": "Sankt Nimmerlein"}) == "Sankt Nimmerlein"


def test_template_liefert_fields_und_parameter():
    fields = anwenden("recherche-digest", {"titel": "Security-News"})
    assert fields["kind"] == "prompt" and fields["result_html"] is True
    assert fields["args"]["titel"] == "Security-News"
    # Defaults that are not overridden are kept.
    assert fields["args"]["sprache"] == "Deutsch" and fields["args"]["quellen"]


def test_template_rendert_ohne_offene_platzhalter():
    """A template that leaves gaps out of the box would be a trap."""
    fields = anwenden("recherche-digest")
    assert offene_platzhalter(fields["prompt"], fields["args"]) == []
    text = rendere(fields["prompt"], fields["args"])
    assert "{{" not in text and "Hacker News" in text


def test_unbekannte_template():
    with pytest.raises(KeyError):
        anwenden("gibtsnicht")


def test_listing_zeigt_parameter():
    entries = {v["key"]: v for v in listing()}
    assert set(entries) == set(JOB_TEMPLATES)
    assert "quellen" in entries["recherche-digest"]["params"]
