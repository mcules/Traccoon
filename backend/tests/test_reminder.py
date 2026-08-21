"""Following up while it still helps.

On 2026-08-07 UNI-12 read around 190 files in three runs and wrote not a line. Tools and
rights were there and no call failed; the agent simply never got to the point. The only
reminder in the run came at round 78 of 80, long after the time was gone. These tests record
when and how following up happens earlier.
"""
from app.worker.runtime import (
    RESULT_TOOLS, REMINDER_BEI, reminder_text, ermahnungen_due,
)


def test_no_nagging_at_the_beginning():
    assert ermahnungen_due(0.0, 0) == 0
    assert ermahnungen_due(REMINDER_BEI[0] - 0.01, 0) == 0


def test_the_first_reminder_well_before_the_end():
    """The point of the exercise: early enough for the run to still do something with it."""
    assert REMINDER_BEI[0] <= 0.5
    assert ermahnungen_due(REMINDER_BEI[0], 0) == 1


def test_each_threshold_only_once():
    """Following up twice is guidance, on every round it is noise."""
    assert ermahnungen_due(0.9, 1) == 2
    assert ermahnungen_due(0.99, 2) == 2      # nothing due any more
    assert ermahnungen_due(1.0, 2) == len(REMINDER_BEI)


def test_a_jump_over_both_thresholds_catches_up_on_both():
    """A single very long tool call must not swallow a threshold."""
    assert ermahnungen_due(0.9, 0) == 2


def test_the_developer_is_sent_to_write():
    weich = reminder_text("execute", 0.35, scharf=False)
    hart = reminder_text("execute", 0.65, scharf=True)
    assert "35 %" in weich and "noch keine Änderung geschrieben" in weich
    assert "JETZT" in hart and "kleinsten sinnvollen Änderung" in hart
    assert "continue_later" in hart and "ask_human" in hart
    assert "submit_plan" not in hart              # that is the business of the architect


def test_the_architect_is_sent_to_plan():
    hart = reminder_text("plan", 0.65, scharf=True)
    assert "submit_plan" in hart
    assert "Änderung" not in hart                 # er schreibt keinen Code
    assert "offen" in hart                        # better a plan with uncertainties


def test_the_result_differs_per_mode():
    assert RESULT_TOOLS["execute"] == {"fs_write", "fs_edit"}
    assert RESULT_TOOLS["plan"] == {"submit_plan"}
    # An agent without write permission gets no reminder at all: the intersection with the
    # offered tools is empty, and whoever may not write should not either.
    angeboten = {"fs_read", "fs_list", "codegraph"}
    assert not (RESULT_TOOLS["execute"] & angeboten)
