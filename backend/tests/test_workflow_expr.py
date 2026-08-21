"""Expressions in templates: making the form the target system wants out of data.

The core of these tests is not "computes correctly" but: a crooked template must never
topple a run, and everything that worked before keeps working.
"""
import datetime as dt

import pytest
from app.services.workflow_expr import evaluate, fill, catalog

pytestmark = pytest.mark.asyncio

CTX = {
    "mail": {"subject": "Ihre Domain-Rechnung wartet auf Bearbeitung", "from": ""},
    "spam": {"score": 0.9123, "aktiv": True},
    "tool": {"json": {"items": [{"name": "eins"}, {"name": "zwei"}]}},
    "leer": {"wert": None},
}


async def test_a_plain_path_behaves_as_before():
    """All existing templates stay valid; otherwise this would be a break, not an extension."""
    assert fill("{{ mail.subject }}", CTX) == CTX["mail"]["subject"]
    assert fill("Betreff: {{mail.subject}}!", CTX).startswith("Betreff: Ihre")
    # An unknown field stays empty instead of writing "None".
    assert fill("[{{ gibts.nicht }}]", CTX) == "[]"


async def test_the_filter_chain_is_applied_left_to_right():
    assert fill("{{ spam.score | times:100 | round:1 }}", CTX) == "91.2"
    assert fill("{{ mail.subject | truncate:12 }}", CTX) == "Ihre Domain…"
    assert fill("{{ mail.subject | lower | truncate:11,\"\" }}", CTX) == "ihre domain"


async def test_the_default_applies_only_when_empty():
    assert fill('{{ mail.from | default:"unbekannt" }}', CTX) == "unbekannt"
    assert fill('{{ leer.wert | default:"x" }}', CTX) == "x"
    assert fill('{{ mail.subject | default:"x" | truncate:4,"" }}', CTX) == "Ihre"


async def test_lists_and_deep_paths():
    assert fill("{{ tool.json.items | count }}", CTX) == "2"
    assert fill("{{ tool.json.items.0.name }}", CTX) == "eins"
    assert fill('{{ tool.json.items | first | json }}', CTX) == '{"name": "eins"}'


async def test_computing_and_formatting_timestamps():
    today = dt.datetime.now(tz=dt.timezone.utc)
    assert fill('{{ now | date:"%Y" }}', CTX) == str(today.year)
    # Two hours later is another point in time: what is checked is the shift itself.
    before = evaluate("now", CTX)
    nachher = evaluate('jetzt | add_time:2,"h"', CTX)
    assert dt.datetime.fromisoformat(nachher) - dt.datetime.fromisoformat(before) \
        >= dt.timedelta(hours=1, minutes=59)
    # An ISO timestamp from the context can be formatted just as well.
    assert fill('{{ zeit | date:"%d.%m.%Y" }}',
                   {"zeit": "2026-08-18T06:31:32+00:00"}) == "18.08.2026"


async def test_a_crooked_template_topples_no_run():
    """A typo in the filter, a text instead of a number: that may at most give an ugly result,
    never an abort in the middle of the flow."""
    assert fill("{{ mail.subject | gibtsnicht }}", CTX) == CTX["mail"]["subject"]
    assert fill("{{ mail.subject | times:2 }}", CTX) == "0.0"
    assert fill("{{ }}", CTX) == ""
    assert fill("{{ tool.json | truncate:5 }}", CTX).endswith("…")


async def test_boolean_values_read_as_expected():
    assert fill("{{ spam.aktiv }}", CTX) == "true"


async def test_the_catalog_explains_every_filter():
    """The list feeds the help in the editor: a filter without an explanation is worthless there."""
    entries = catalog()
    assert {"truncate", "default", "date", "count", "times"} <= {e["name"] for e in entries}
    assert all(e["hilfe"] for e in entries)


async def test_a_filter_argument_may_come_from_the_context():
    """`default:event.type` should insert the value from there, not the word.

    Before, "event.type" stood literally in the Telegram message, and in the throttle key,
    which would have made all kinds of disturbance the same case.
    """
    ctx = {"event": {"type": "deviceInactive", "attributes": {}}}
    assert fill("{{ event.attributes.alarm | default:event.type }}", ctx) == "deviceInactive"


async def test_a_quoted_argument_stays_literal():
    ctx = {"event": {"type": "alarm"}}
    assert fill('{{ fehlt | default:"event.type" }}', ctx) == "event.type"


async def test_an_unknown_path_stays_as_text():
    """A default like `default:unbekannt` behaves unchanged."""
    assert fill("{{ fehlt | default:unbekannt }}", {}) == "unbekannt"


async def test_numbers_stay_numbers():
    assert fill("{{ text | truncate:4 }}", {"text": "abcdefgh"}) == "abc…"


# --- Listen und Pfade ------------------------------------------------------------------

async def test_field_pulls_from_a_list_of_objects():
    """The way from a search hit to a sentence: without it a hit list stays unusable."""
    ctx = {"t": {"hits": [{"filename": "a/VW T5.md", "x": 1}, {"filename": "b/Corsa C.md"}]}}
    assert fill('{{ t.hits | field:"filename" | join:", " }}', ctx) == "a/VW T5.md, b/Corsa C.md"
    # Einzelnes Objekt: derselbe Filter, ein Wert.
    assert fill('{{ t | field:"hits" | count }}', ctx) == "2"


async def test_field_skips_what_it_does_not_have():
    ctx = {"l": [{"a": 1}, {"b": 2}, "kein Objekt"]}
    assert fill('{{ l | field:"a" | join:"," }}', ctx) == "1"


async def test_basename_turns_paths_into_names():
    ctx = {"p": ["03 Bereiche/Fahrzeuge/VW T5 Multivan.md", "Opel Corsa C.md"]}
    assert fill('{{ p | basename | join:" und " }}', ctx) == "VW T5 Multivan und Opel Corsa C"
    assert fill("{{ p | first | basename }}", ctx) == "VW T5 Multivan"


async def test_max_answers_the_question_a_decision_cannot_ask():
    """JSONLogic knows no `some` here; "does any day bring snow" is therefore turned into a
    number first and compared afterwards."""
    ctx = {"w": {"schnee": [0, 0, 1.4, 0.2], "leer": [], "text": ["0,5", "2"]}}
    assert fill("{{ w.schnee | max }}", ctx) == "1.4"
    assert fill("{{ w.schnee | min }}", ctx) == "0.0"
    assert fill("{{ w.leer | max }}", ctx) == "0", "nichts zu vergleichen ist kein Fehler"
    assert fill("{{ w.leer | min }}", ctx) == "0"
    # Numbers as text (JSON APIs like to deliver that) count along.
    assert fill("{{ w.text | max }}", ctx) == "2.0"


async def test_new_filters_stand_in_the_catalog():
    """What the editor does not offer nobody finds."""
    names = {e["name"] for e in catalog()}
    assert {"field", "basename", "max", "min"} <= names
