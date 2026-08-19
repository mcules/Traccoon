"""Do not offer a tool whose answer is already settled.

The deployer rejects every assignment without a stack directory of its own; otherwise
Traccoon would restart itself in the middle of a run. Until now only it noticed that: the
agent created a deployment row, waited every 3 seconds and got the refusal after the detour.
56 of the 186 rows in `deployments` are exactly this refusal; runs 753 and 754 each spent a
turn on it on 2026-08-07.
"""
import pytest

from app.worker.runtime import deploy_gesperrt


def test_ohne_stack_verzeichnis_gesperrt():
    grund = deploy_gesperrt("")
    assert "kein eigenes Stack-Verzeichnis" in grund
    assert "check" in grund          # the agent learns what to do instead


def test_traccoon_selbst_gesperrt(monkeypatch):
    monkeypatch.setenv("SELF_STACK_DIR", "/opt/docker/stacks/traccoon")
    assert "Wartungs-Update" in deploy_gesperrt("/opt/docker/stacks/traccoon/")


def test_fremdes_projekt_darf(monkeypatch):
    monkeypatch.setenv("SELF_STACK_DIR", "/opt/docker/stacks/traccoon")
    assert deploy_gesperrt("/opt/docker/stacks/uniwar") == ""


@pytest.mark.parametrize("selbst", ["", "/opt/docker/stacks/traccoon"])
def test_ohne_gesetzten_selbstpfad_bleibt_das_urteil_stabil(monkeypatch, selbst):
    """The worker container does not know `SELF_STACK_DIR` today, so the empty check has to
    carry on its own, and a set path must not lock foreign targets."""
    monkeypatch.setenv("SELF_STACK_DIR", selbst)
    assert deploy_gesperrt("") != ""
    assert deploy_gesperrt("/opt/docker/stacks/uniwar") == ""
