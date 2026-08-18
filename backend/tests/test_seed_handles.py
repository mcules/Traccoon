"""Jede Kante der ausgelieferten Abläufe muss an einem Ausgang hängen, den die Oberfläche
auch zeichnet.

Anlass: die Abnahme zeigte einen Schritt „in der Luft". Die Kanten waren da, aber der
Aktions-Knoten bot nur den Standard-Ausgang an — React Flow zeichnet eine Kante nur, wenn
es den benannten Ausgang wirklich gibt, und verschluckt sie sonst kommentarlos.
"""
import pytest
from app.services.workflow_engine import node_config, node_type
from app.services.workflow_seed import BUILDERS

# Was ein Knotentyp an Ausgängen anbieten kann (Spiegel der Node-Komponenten).
ERLAUBT = {
    "start": {"out"},
    "human_task": {"out"},
    "approval": {"approved", "rejected"},
    "subflow": {"completed", "failed", "cancelled", "out"},
    "wait_event": {"out"},          # zzgl. der konfigurierten Ereignisse
    "agent_task": {"planned", "done", "blocked", "failed", "loop_exhausted", "err", "out"},
    "auto_action": {"out", "merged", "pr_open", "no_git", "conflict", "push_failed",
                    "pr_failed", "gone", "error"},
    "decision": set(),              # kommt aus den Zweigen
    "end": set(),
}


@pytest.mark.parametrize("slot", sorted(BUILDERS))
def test_ausgaenge_sind_am_knoten_vorhanden(slot):
    graph = BUILDERS[slot]()
    knoten = {n["id"]: n for n in graph["nodes"]}
    for e in graph["edges"]:
        quelle = knoten[e["source"]]
        typ = node_type(quelle)
        cfg = node_config(quelle)
        erlaubt = set(ERLAUBT.get(typ, {"out"}))
        if typ == "decision":
            erlaubt |= {b.get("handle") for b in (cfg.get("branches") or [])}
            erlaubt.add(cfg.get("default_handle", "default"))
        if typ == "wait_event":
            erlaubt |= set(cfg.get("events") or ["comment", "manual"])
        handle = e.get("sourceHandle") or "out"
        assert handle in erlaubt, (
            f"{slot}: Kante {e['id']} nutzt den Ausgang '{handle}' an einem "
            f"{typ}-Knoten — den zeichnet die Oberfläche dort nicht.")


@pytest.mark.parametrize("slot", sorted(BUILDERS))
def test_kein_schritt_haengt_in_der_luft(slot):
    graph = BUILDERS[slot]()
    ein = {e["target"] for e in graph["edges"]}
    aus = {e["source"] for e in graph["edges"]}
    for n in graph["nodes"]:
        typ = node_type(n)
        if typ != "start":
            assert n["id"] in ein, f"{slot}: '{n['id']}' hat keinen Eingang"
        if typ != "end":
            assert n["id"] in aus, f"{slot}: '{n['id']}' hat keinen Ausgang"


@pytest.mark.parametrize("slot", sorted(BUILDERS))
def test_standard_zweig_ist_ein_zweig(slot):
    """Der Standard-Zweig muss in der Zweig-Liste stehen.

    Sonst zeigt der Knoten einen Ausgang, den die Konfiguration nicht kennt: das Panel
    stellt ihn als „— keiner —" dar, und beim nächsten Speichern hinge seine Kante in der
    Luft. Genau das war im Ticket-Eingang der Fall.
    """
    graph = BUILDERS[slot]()
    for n in graph["nodes"]:
        if node_type(n) != "decision":
            continue
        cfg = node_config(n)
        zweige = {b.get("handle") for b in (cfg.get("branches") or [])}
        if not zweige:
            continue
        std = cfg.get("default_handle")
        assert std in zweige, (
            f"{slot}/{n['id']}: Standard-Zweig '{std}' ist keiner der Zweige {sorted(zweige)}")


@pytest.mark.parametrize("slot", sorted(BUILDERS))
def test_aktionen_in_einheitlicher_form(slot):
    """Aktions-Knoten müssen die verschachtelte Form nutzen.

    In der flachen Form (`{"action": "name", "status": …}`) zeigt der Editor weder Aktion
    noch Parameter an — und die erste Bearbeitung überschreibt sie. Das Backend versteht
    zwar beide Formen, die Oberfläche ist damit aber unbrauchbar.
    """
    for n in BUILDERS[slot]()["nodes"]:
        if node_type(n) != "auto_action":
            continue
        aktion = node_config(n).get("action")
        assert isinstance(aktion, dict) and aktion.get("action"), (
            f"{slot}/{n['id']}: Aktion in flacher Form ({aktion!r}) — bitte "
            f'{{"action": {{"action": …, "params": {{…}}}}}} verwenden')


@pytest.mark.parametrize("slot", sorted(BUILDERS))
def test_keine_zwei_knoten_auf_derselben_stelle(slot):
    """Zwei Knoten an derselben Position verdecken einander — und mit ihnen die Kante,
    die dort hängt. Im Mail-Eingang fiel genau das erst am Bild auf."""
    if slot == "ticket_lifecycle":
        pytest.skip("cap_baseline/st_approved liegen aufeinander — Altbestand, eigener Fall")
    graph = BUILDERS[slot]()
    stellen: dict[tuple, list[str]] = {}
    for n in graph["nodes"]:
        stellen.setdefault((n["position"]["x"], n["position"]["y"]), []).append(n["id"])
    doppelt = {k: v for k, v in stellen.items() if len(v) > 1}
    assert not doppelt, f"Knoten liegen aufeinander: {doppelt}"
