"""Following up while it still helps.

On 2026-08-07 ABC-12 read around 190 files in three runs and wrote not a line. Tools and
rights were there and no call failed; the agent simply never got to the point. The only
reminder in the run came at round 78 of 80, long after the time was gone. These tests record
when and how following up happens earlier.
"""
from app.worker.runtime import (
    ERGEBNIS_TOOLS, ERMAHNUNG_BEI, ermahnung_text, ermahnungen_faellig,
)


def test_am_anfang_wird_nicht_genoergelt():
    assert ermahnungen_faellig(0.0, 0) == 0
    assert ermahnungen_faellig(ERMAHNUNG_BEI[0] - 0.01, 0) == 0


def test_erste_ermahnung_deutlich_vor_schluss():
    """The point of the exercise: early enough for the run to still do something with it."""
    assert ERMAHNUNG_BEI[0] <= 0.5
    assert ermahnungen_faellig(ERMAHNUNG_BEI[0], 0) == 1


def test_jede_schwelle_nur_einmal():
    """Following up twice is guidance, on every round it is noise."""
    assert ermahnungen_faellig(0.9, 1) == 2
    assert ermahnungen_faellig(0.99, 2) == 2      # nothing due any more
    assert ermahnungen_faellig(1.0, 2) == len(ERMAHNUNG_BEI)


def test_sprung_ueber_beide_schwellen_holt_beide_nach():
    """A single very long tool call must not swallow a threshold."""
    assert ermahnungen_faellig(0.9, 0) == 2


def test_entwickler_wird_zum_schreiben_geschickt():
    weich = ermahnung_text("execute", 0.35, scharf=False)
    hart = ermahnung_text("execute", 0.65, scharf=True)
    assert "35 %" in weich and "noch keine Änderung geschrieben" in weich
    assert "JETZT" in hart and "kleinsten sinnvollen Änderung" in hart
    assert "continue_later" in hart and "ask_human" in hart
    assert "submit_plan" not in hart              # that is the business of the architect


def test_architekt_wird_zum_plan_geschickt():
    hart = ermahnung_text("plan", 0.65, scharf=True)
    assert "submit_plan" in hart
    assert "Änderung" not in hart                 # er schreibt keinen Code
    assert "offen" in hart                        # better a plan with uncertainties


def test_ergebnis_ist_je_modus_etwas_anderes():
    assert ERGEBNIS_TOOLS["execute"] == {"fs_write", "fs_edit"}
    assert ERGEBNIS_TOOLS["plan"] == {"submit_plan"}
    # An agent without write permission gets no reminder at all: the intersection with the
    # offered tools is empty, and whoever may not write should not either.
    angeboten = {"fs_read", "fs_list", "codegraph"}
    assert not (ERGEBNIS_TOOLS["execute"] & angeboten)
