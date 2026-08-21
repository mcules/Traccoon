"""Placeholders and templates for jobs.

The occasion: the AI and tech news job carried topic, sources and structure firmly in the prompt. A second digest would have been a copy. Now: a template plus parameters (`jobs.args` as an object).
"""
import datetime as dt

import pytest
from app.services.job_params import STD_TZ as TZ, open_placeholder, render
from app.services.job_templates import JOB_TEMPLATES, apply, listing


def test_parameters_are_substituted():
    assert render("Thema: {{thema}}", {"thema": "Funk"}) == "Thema: Funk"


def test_a_list_becomes_an_enumeration():
    assert render("{{quellen}}", {"quellen": ["a", "b"]}) == "a, b"


def test_an_unknown_placeholder_stays():
    """Emptying silently would remove a rule from the assignment without a sound; visibly
    wrong is better than invisibly wrong."""
    assert render("Quellen: {{quellen}}", {"thema": "x"}) == "Quellen: {{quellen}}"
    assert open_placeholder("Quellen: {{quellen}}", {"thema": "x"}) == ["quellen"]


def test_script_arguments_stay_untouched():
    """`args` is historically the argument list of the script jobs. Only an object is a
    parameter set; a list must replace nothing."""
    assert render("{{thema}}", ["--flag", "wert"]) == "{{thema}}"


def test_the_time_window_comes_from_the_last_run():
    now = dt.datetime(2026, 7, 29, 8, 0, tzinfo=dt.timezone.utc)
    last = dt.datetime(2026, 7, 28, 8, 0, tzinfo=dt.timezone.utc)
    text = render("{{window}} · {{today}} · {{since}}", {}, now=now, last_run=last)
    assert "2026-07-28 10:00 bis 2026-07-29 10:00" in text   # Europe/Berlin (UTC+2)
    assert "2026-07-29" in text and TZ.key == "Europe/Berlin"


def test_without_a_last_run_24_hours_back():
    """First run or the job was off: a digest still needs a lower bound."""
    now = dt.datetime(2026, 7, 29, 6, 0, tzinfo=dt.timezone.utc)
    assert "2026-07-28 08:00 bis 2026-07-29 08:00" in render("{{window}}", {}, now=now)


def test_an_own_parameter_beats_a_builtin_one():
    assert render("{{today}}", {"today": "Sankt Nimmerlein"}) == "Sankt Nimmerlein"


def test_the_template_delivers_fields_and_parameters():
    fields = apply("recherche-digest", {"titel": "Security-News"})
    assert fields["kind"] == "prompt" and fields["result_html"] is True
    assert fields["args"]["titel"] == "Security-News"
    # Defaults that are not overridden are kept.
    assert fields["args"]["sprache"] == "Deutsch" and fields["args"]["quellen"]


def test_the_template_renders_without_open_placeholders():
    """A template that leaves gaps out of the box would be a trap."""
    fields = apply("recherche-digest")
    assert open_placeholder(fields["prompt"], fields["args"]) == []
    text = render(fields["prompt"], fields["args"])
    assert "{{" not in text and "Hacker News" in text


def test_an_unknown_template():
    with pytest.raises(KeyError):
        apply("gibtsnicht")


def test_the_listing_shows_parameters():
    entries = {v["key"]: v for v in listing()}
    assert set(entries) == set(JOB_TEMPLATES)
    assert "quellen" in entries["recherche-digest"]["params"]
