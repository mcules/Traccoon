"""Die Digest-Seite bei einem fehlgeschlagenen Lauf.

Anlass: Job #3 ging seit dem 03.08. jeden Tag auf `error` — die Telegram-Meldung verlinkte
`/digest/<run-id>`, dort lag aber nie ein Digest, weil der Lauf nie fertig wurde. Die Seite
zeigte eine leere Seite statt des Fehlers — von außen nicht diagnostizierbar.
"""
import pytest
from app.main import digest
from app.models.ops import JobRun


async def test_erfolgreicher_lauf_zeigt_den_digest(db):
    jr = JobRun(job_id=1, status="ok", output="# Die Nachrichten von heute\n\nAlles ruhig.")
    db.add(jr)
    await db.commit()
    resp = await digest(jr.id)
    assert "Alles ruhig" in resp.body.decode()
    assert "fehlgeschlagen" not in resp.body.decode()


async def test_fehlgeschlagener_lauf_zeigt_den_fehler_statt_leerer_seite(db):
    jr = JobRun(job_id=1, status="error", output="", error="Web-Search-Tool: rate limited (429)")
    db.add(jr)
    await db.commit()
    resp = await digest(jr.id)
    text = resp.body.decode()
    assert "rate limited" in text
    assert "fehlgeschlagen" in text.lower()


async def test_fehlgeschlagener_lauf_zeigt_nur_die_erste_fehlerzeile(db):
    """Diese Seite ist ABSICHTLICH ohne Login erreichbar, `run_id` ist eine erratbare
    fortlaufende Ganzzahl — der volle Fehlertext (interne Exception-Details, Provider-
    Fehlerkörper) darf hier NICHT ungeschützt ins Netz. Nur die erste Zeile."""
    jr = JobRun(job_id=1, status="error", output="",
                error="rate limited (429)\nTraceback (most recent call last):\n"
                      "  File \"secret/internal/path.py\", line 42, in _do\ninterne Details hier")
    db.add(jr)
    await db.commit()
    resp = await digest(jr.id)
    text = resp.body.decode()
    assert "rate limited (429)" in text
    assert "Traceback" not in text and "secret/internal/path.py" not in text


async def test_fehlgeschlagener_lauf_ohne_fehlertext_bleibt_lesbar(db):
    jr = JobRun(job_id=1, status="error", output="", error="")
    db.add(jr)
    await db.commit()
    resp = await digest(jr.id)
    assert "Kein Fehlertext hinterlegt" in resp.body.decode()


async def test_erschoepfter_lauf_zeigt_teilergebnis_und_grund(db):
    """Review-Befund 2026-08-07: `loop_exhausted` füllt `jr.output` mit einem Teilergebnis
    (s. `_handle_job`) — nur den Fehler zu zeigen und das Teilergebnis zu verschlucken wäre
    wieder das Ausgangssymptom (Link zeigt „kein Digest", obwohl einer da ist)."""
    jr = JobRun(job_id=1, status="error",
                output="# Die Nachrichten von heute\n\nEiniges gefunden, aber nicht alles.",
                error="Nach 4 Fortsetzungsrunde(n) noch nicht fertig (loop_exhausted)")
    db.add(jr)
    await db.commit()
    resp = await digest(jr.id)
    text = resp.body.decode()
    assert "Einiges gefunden" in text                          # Teilergebnis bleibt sichtbar
    assert "Nach 4 Fortsetzungsrunde(n)" in text                # Grund steht dabei
    assert "unvollständig" in text.lower()


async def test_unbekannter_lauf_bleibt_404(db):
    resp = await digest(999999)
    assert resp.status_code == 404
