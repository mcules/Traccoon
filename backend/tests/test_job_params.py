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
    assert "2026-07-28 10:00 to 2026-07-29 10:00" in text   # Europe/Berlin (UTC+2)
    assert "2026-07-29" in text and TZ.key == "Europe/Berlin"


def test_without_a_last_run_24_hours_back():
    """First run or the job was off: a digest still needs a lower bound."""
    now = dt.datetime(2026, 7, 29, 6, 0, tzinfo=dt.timezone.utc)
    assert "2026-07-28 08:00 to 2026-07-29 08:00" in render("{{window}}", {}, now=now)


def test_an_own_parameter_beats_a_builtin_one():
    assert render("{{today}}", {"today": "Sankt Nimmerlein"}) == "Sankt Nimmerlein"


def test_the_template_delivers_fields_and_parameters():
    fields = apply("research-digest", {"ablage": "security-news"})
    # Since the research jobs share ONE flow, a template no longer brings a prompt but the
    # start context of that flow.
    assert fields["kind"] == "workflow" and fields["workflow_key"] == "recherche"
    assert fields["args"]["ablage"] == "security-news"
    # Defaults that are not overridden are kept.
    assert fields["args"]["agent"] == "news" and fields["args"]["auftrag"]


def test_the_assignment_carries_no_placeholders():
    """`{{…}}` is replaced ONE round: braces inside the assignment would stay put."""
    for key in ("research-digest", "research-watch"):
        assert "{{" not in apply(key)["args"]["auftrag"], key


def test_the_watcher_stays_silent_by_default_and_the_digest_files():
    watch, digest = apply("research-watch")["args"], apply("research-digest")["args"]
    assert watch["still_wenn"] and not watch["ablage"]
    assert digest["ablage"] and not digest["still_wenn"]
    # The word the flow watches for has to be IN the assignment, otherwise the job reports
    # every morning that there is nothing to report.
    assert watch["still_wenn"] in watch["auftrag"]


def test_an_unknown_template():
    with pytest.raises(KeyError):
        apply("gibtsnicht")


def test_the_listing_shows_parameters():
    entries = {v["key"]: v for v in listing()}
    assert set(entries) == set(JOB_TEMPLATES)
    assert "auftrag" in entries["research-digest"]["params"]
