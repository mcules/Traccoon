"""Following up while it still helps.

On 2026-08-07 UNI-12 read around 190 files in three runs and wrote not a line. Tools and
rights were there and no call failed; the agent simply never got to the point. The only
reminder in the run came at round 78 of 80, long after the time was gone. These tests record
when and how following up happens earlier.
"""
from app.worker.runtime import (
    RESULT_TOOLS, REMINDER_AT, reminder_text, reminders_due,
)


def test_no_nagging_at_the_beginning():
    assert reminders_due(0.0, 0) == 0
    assert reminders_due(REMINDER_AT[0] - 0.01, 0) == 0


def test_the_first_reminder_well_before_the_end():
    """The point of the exercise: early enough for the run to still do something with it."""
    assert REMINDER_AT[0] <= 0.5
    assert reminders_due(REMINDER_AT[0], 0) == 1


def test_each_threshold_only_once():
    """Following up twice is guidance, on every round it is noise."""
    assert reminders_due(0.9, 1) == 2
    assert reminders_due(0.99, 2) == 2      # nothing due any more
    assert reminders_due(1.0, 2) == len(REMINDER_AT)


def test_a_jump_over_both_thresholds_catches_up_on_both():
    """A single very long tool call must not swallow a threshold."""
    assert reminders_due(0.9, 0) == 2


def test_the_developer_is_sent_to_write():
    soft = reminder_text("execute", 0.35, sharp=False)
    hard = reminder_text("execute", 0.65, sharp=True)
    assert "35 %" in soft and "not written a change yet" in soft
    assert "NOW" in hard and "smallest sensible change" in hard
    assert "continue_later" in hard and "ask_human" in hard
    assert "submit_plan" not in hard              # that is the business of the architect


def test_the_architect_is_sent_to_plan():
    hard = reminder_text("plan", 0.65, sharp=True)
    assert "submit_plan" in hard
    assert "change" not in hard                   # it writes no code
    assert "open" in hard                         # better a plan with uncertainties


def test_the_result_differs_per_mode():
    assert RESULT_TOOLS["execute"] == {"fs_write", "fs_edit"}
    assert RESULT_TOOLS["plan"] == {"submit_plan"}
    # An agent without write permission gets no reminder at all: the intersection with the
    # offered tools is empty, and whoever may not write should not either.
    angeboten = {"fs_read", "fs_list", "codegraph"}
    assert not (RESULT_TOOLS["execute"] & angeboten)
