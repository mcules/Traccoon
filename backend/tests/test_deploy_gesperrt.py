"""Kein Werkzeug anbieten, dessen Antwort schon feststeht.

Der Deployer lehnt jeden Auftrag ohne eigenes Stack-Verzeichnis ab — sonst würde Traccoon
sich mitten im Lauf selbst neu starten. Gemerkt hat das bisher erst er: der Agent legte eine
Deployment-Zeile an, wartete im 3-Sekunden-Takt und bekam die Absage nach dem Umweg. 56 der
186 Zeilen in `deployments` sind genau diese Absage; die Läufe 753 und 754 haben am
2026-08-07 je einen Zug dafür verbraucht.
"""
import pytest

from app.worker.runtime import deploy_gesperrt


def test_ohne_stack_verzeichnis_gesperrt():
    grund = deploy_gesperrt("")
    assert "kein eigenes Stack-Verzeichnis" in grund
    assert "check" in grund          # der Agent erfährt, was stattdessen zu tun ist


def test_traccoon_selbst_gesperrt(monkeypatch):
    monkeypatch.setenv("SELF_STACK_DIR", "/opt/docker/stacks/traccoon")
    assert "Wartungs-Update" in deploy_gesperrt("/opt/docker/stacks/traccoon/")


def test_fremdes_projekt_darf(monkeypatch):
    monkeypatch.setenv("SELF_STACK_DIR", "/opt/docker/stacks/traccoon")
    assert deploy_gesperrt("/opt/docker/stacks/uniwar") == ""


@pytest.mark.parametrize("selbst", ["", "/opt/docker/stacks/traccoon"])
def test_ohne_gesetzten_selbstpfad_bleibt_das_urteil_stabil(monkeypatch, selbst):
    """Der Worker-Container kennt `SELF_STACK_DIR` heute nicht — die leere Prüfung muss
    deshalb für sich allein tragen, und ein gesetzter Pfad darf fremde Ziele nicht sperren."""
    monkeypatch.setenv("SELF_STACK_DIR", selbst)
    assert deploy_gesperrt("") != ""
    assert deploy_gesperrt("/opt/docker/stacks/uniwar") == ""
