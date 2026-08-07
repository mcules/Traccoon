"""Nachhaken, solange es noch etwas nützt.

ABC-12 hat am 2026-08-07 in drei Läufen rund 190 Dateien gelesen und keine Zeile
geschrieben. Werkzeuge und Rechte waren da, kein Aufruf schlug fehl — der Agent kam nur nie
zum Punkt. Die einzige Ermahnung im Lauf kam bei Runde 78 von 80, also lange nachdem die
Zeit weg war. Diese Tests halten fest, wann und wie früher nachgehakt wird.
"""
from app.worker.runtime import (
    ERGEBNIS_TOOLS, ERMAHNUNG_BEI, ermahnung_text, ermahnungen_faellig,
)


def test_am_anfang_wird_nicht_genoergelt():
    assert ermahnungen_faellig(0.0, 0) == 0
    assert ermahnungen_faellig(ERMAHNUNG_BEI[0] - 0.01, 0) == 0


def test_erste_ermahnung_deutlich_vor_schluss():
    """Der Sinn der Übung: früh genug, dass der Lauf noch etwas damit anfangen kann."""
    assert ERMAHNUNG_BEI[0] <= 0.5
    assert ermahnungen_faellig(ERMAHNUNG_BEI[0], 0) == 1


def test_jede_schwelle_nur_einmal():
    """Zweimal nachhaken ist Führung, bei jeder Runde ist es Rauschen."""
    assert ermahnungen_faellig(0.9, 1) == 2
    assert ermahnungen_faellig(0.99, 2) == 2      # nichts mehr fällig
    assert ermahnungen_faellig(1.0, 2) == len(ERMAHNUNG_BEI)


def test_sprung_ueber_beide_schwellen_holt_beide_nach():
    """Ein einziger sehr langer Werkzeug-Aufruf darf keine Schwelle verschlucken."""
    assert ermahnungen_faellig(0.9, 0) == 2


def test_entwickler_wird_zum_schreiben_geschickt():
    weich = ermahnung_text("execute", 0.35, scharf=False)
    hart = ermahnung_text("execute", 0.65, scharf=True)
    assert "35 %" in weich and "noch keine Änderung geschrieben" in weich
    assert "JETZT" in hart and "kleinsten sinnvollen Änderung" in hart
    assert "continue_later" in hart and "ask_human" in hart
    assert "submit_plan" not in hart              # das ist Sache des Architekten


def test_architekt_wird_zum_plan_geschickt():
    hart = ermahnung_text("plan", 0.65, scharf=True)
    assert "submit_plan" in hart
    assert "Änderung" not in hart                 # er schreibt keinen Code
    assert "offen" in hart                        # lieber ein Plan mit Unsicherheiten


def test_ergebnis_ist_je_modus_etwas_anderes():
    assert ERGEBNIS_TOOLS["execute"] == {"fs_write", "fs_edit"}
    assert ERGEBNIS_TOOLS["plan"] == {"submit_plan"}
    # Ein Agent ohne Schreibrecht bekommt gar keine Ermahnung: die Schnittmenge mit den
    # angebotenen Werkzeugen ist leer, und wer nicht schreiben darf, soll auch nicht.
    angeboten = {"fs_read", "fs_list", "codegraph"}
    assert not (ERGEBNIS_TOOLS["execute"] & angeboten)
