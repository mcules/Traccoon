"""Ticking a task off, and what comes back when it recurs."""
from __future__ import annotations

import datetime as dt

from app.notes.dv import recurrence as rc, tasktoggle as tt

TODAY = dt.date(2026, 9, 3)


# --------------------------------------------------------------- the tick

def test_a_box_is_flipped(monkeypatch) -> None:
    assert tt.toggled("- [ ] Etwas", True, "dataview", TODAY) == ["- [x] Etwas"]
    assert tt.toggled("- [x] Etwas", False, "dataview", TODAY) == ["- [ ] Etwas"]


def test_a_task_block_writes_the_day_it_was_done() -> None:
    """A query result only flips the box — that language's own completion
    tracking is off — while a task block records the day."""
    assert tt.toggled("- [ ] Etwas", True, "tasks", TODAY) == ["- [x] Etwas ✅ 2026-09-03"]
    assert tt.toggled("- [ ] Etwas", True, "dataview", TODAY) == ["- [x] Etwas"]


def test_unticking_takes_the_marks_off_again() -> None:
    assert tt.toggled("- [x] Etwas ✅ 2026-09-03", False, "tasks", TODAY) == ["- [ ] Etwas"]
    assert tt.toggled("- [x] Etwas ❌ 2026-09-03", False, "tasks", TODAY) == ["- [ ] Etwas"]


def test_a_line_that_is_not_a_task_gives_nothing() -> None:
    assert tt.toggled("nur Text", True) is None
    assert tt.text_of("nur Text") is None
    assert tt.is_task_line("  * [ ] auch eine") is True


# ---------------------------------------------------------------- recurrence

def test_a_recurring_task_comes_back() -> None:
    out = tt.toggled("- [ ] Wöchentlich 🔁 every week 📅 2026-09-03", True, "tasks", TODAY)
    assert out == ["- [ ] Wöchentlich 🔁 every week 📅 2026-09-10",
                   "- [x] Wöchentlich 🔁 every week 📅 2026-09-03 ✅ 2026-09-03"]


def test_the_rules_this_vault_uses() -> None:
    def nxt(rule: str, due: str) -> str | None:
        line = f"- [ ] X 🔁 {rule} 📅 {due}"
        out = rc.next_instance(line, TODAY)
        return out.split("📅 ")[1] if out else None

    assert nxt("every day", "2026-09-03") == "2026-09-04"
    assert nxt("every week", "2026-09-03") == "2026-09-10"
    assert nxt("every 2 weeks", "2026-09-03") == "2026-09-17"
    assert nxt("every month", "2026-09-03") == "2026-10-03"
    assert nxt("every year", "2026-09-03") == "2027-09-03"
    assert nxt("every monday", "2026-09-03") == "2026-09-07"
    assert nxt("every year on July 31", "2026-09-03") == "2027-07-31"


def test_a_recurrence_across_the_clock_change_keeps_its_day() -> None:
    """The side this replaces works in milliseconds: three months from the
    seventh of October lands at eleven at night on the sixth of January, because
    an hour goes missing when the clocks change, and it then reads off the
    previous day."""
    line = "- [ ] Quartalsreview 🔁 every 3 months 📅 2026-10-07"
    assert rc.next_instance(line, TODAY).endswith("2027-01-07")


def test_the_thirty_first_lands_on_a_day_that_exists() -> None:
    line = "- [ ] X 🔁 every month 📅 2026-01-31"
    assert rc.next_instance(line, TODAY).endswith("2026-02-28")


def test_when_done_counts_from_today_instead() -> None:
    line = "- [ ] X 🔁 every week when done 📅 2026-01-01"
    assert rc.next_instance(line, TODAY).endswith("2026-09-10")


def test_every_date_moves_by_the_same_amount() -> None:
    """A task scheduled three days before it is due keeps that spacing."""
    line = "- [ ] X 🔁 every week ⏳ 2026-09-01 📅 2026-09-04"
    out = rc.next_instance(line, TODAY)
    assert "⏳ 2026-09-08" in out and "📅 2026-09-11" in out


def test_the_new_instance_carries_no_completion_mark() -> None:
    line = "- [ ] X 🔁 every week 📅 2026-09-03 ✅ 2026-09-03"
    assert "✅" not in rc.next_instance(line, TODAY)


def test_a_rule_that_is_not_understood_brings_nothing_back() -> None:
    """Better than a task that returns on the wrong day: not coming back is
    visible, a wrong date is not."""
    assert rc.next_instance("- [ ] X 🔁 every second tuesday of the month 📅 2026-09-03",
                            TODAY) is None
    assert rc.parse("alle sieben Tage") is None
    assert rc.parse("") is None


def test_a_task_without_a_rule_does_not_recur() -> None:
    assert rc.next_instance("- [ ] X 📅 2026-09-03", TODAY) is None
