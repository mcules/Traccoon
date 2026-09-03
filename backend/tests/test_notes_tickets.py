"""The short ticket a browser sends when it cannot send a header."""
from __future__ import annotations

import time

from app.notes import tickets


def test_a_ticket_names_who_it_belongs_to() -> None:
    assert tickets.holder(tickets.issue(42)) == 42


def test_a_changed_ticket_is_worth_nothing() -> None:
    ticket = tickets.issue(42)
    body, sig = ticket.rsplit(".", 1)
    # Somebody else's number, with the signature that went with mine.
    assert tickets.holder(f"9.{body.split('.', 1)[1]}.{sig}") is None
    assert tickets.holder(f"{body}.{'0' * len(sig)}") is None
    assert tickets.holder("nonsense") is None
    assert tickets.holder("") is None


def test_a_ticket_runs_out(monkeypatch) -> None:
    ticket = tickets.issue(42)
    later = time.time() + tickets.TTL + 10
    monkeypatch.setattr(time, "time", lambda: later)
    assert tickets.holder(ticket) is None


def test_both_halves_of_the_note_area_get_one() -> None:
    """A cookie on `/api/notes` is not sent to `/api/notes-native`: path
    matching is about slashes, not about intent. Reading it wrong means no
    pictures and no templates on the side that has moved."""
    assert "/api/notes" in tickets.PATHS and "/api/notes-native" in tickets.PATHS
    assert not "/api/notes-native".startswith("/api/notes/")
