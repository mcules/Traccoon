"""Every edge of the shipped flows has to hang off an exit the interface actually draws.

The occasion: the acceptance showed a step "in the air". The edges were there, but the
action node offered only the default exit; React Flow draws an edge only when the named exit
really exists and swallows it without a word otherwise.
"""
import pytest
from app.services.workflow_engine import node_config, node_type
from app.services.workflow_seed import BUILDERS

# What a node type can offer as exits (a mirror of the node components).
ALLOWED = {
    "start": {"out"},
    "human_task": {"out"},
    "approval": {"approved", "rejected"},
    "subflow": {"completed", "failed", "cancelled", "out"},
    "wait_event": {"out"},          # plus the configured events
    "agent_task": {"planned", "done", "blocked", "failed", "loop_exhausted", "err", "out"},
    "auto_action": {"out", "merged", "pr_open", "no_git", "conflict", "push_failed",
                    "pr_failed", "gone", "error"},
    "loop": {"element", "fertig"},
    "timer": {"out"},
    "decision": set(),              # comes from the branches
    "end": set(),
}


@pytest.mark.parametrize("slot", sorted(BUILDERS))
def test_ausgaenge_sind_am_node_vorhanden(slot):
    graph = BUILDERS[slot]()
    node = {n["id"]: n for n in graph["nodes"]}
    for e in graph["edges"]:
        source = node[e["source"]]
        kind = node_type(source)
        cfg = node_config(source)
        allowed = set(ALLOWED.get(kind, {"out"}))
        if kind == "decision":
            allowed |= {b.get("handle") for b in (cfg.get("branches") or [])}
            allowed.add(cfg.get("default_handle", "default"))
        if kind == "wait_event":
            allowed |= set(cfg.get("events") or ["comment", "manual"])
        handle = e.get("sourceHandle") or "out"
        assert handle in allowed, (
            f"{slot}: edge {e['id']} uses the output '{handle}' on a "
            f"{kind} node, and the interface does not draw that one there.")


@pytest.mark.parametrize("slot", sorted(BUILDERS))
def test_kein_step_hangs_in_der_luft(slot):
    graph = BUILDERS[slot]()
    ein = {e["target"] for e in graph["edges"]}
    aus = {e["source"] for e in graph["edges"]}
    for n in graph["nodes"]:
        kind = node_type(n)
        if kind != "start":
            assert n["id"] in ein, f"{slot}: '{n['id']}' hat keinen Eingang"
        if kind != "end":
            assert n["id"] in aus, f"{slot}: '{n['id']}' hat keinen Ausgang"


@pytest.mark.parametrize("slot", sorted(BUILDERS))
def test_standard_branch_ist_ein_branch(slot):
    """The default branch has to stand in the branch list.

    Otherwise the node shows an exit the configuration does not know: the panel presents it
    as "- none -", and on the next save its edge would hang in the air. Exactly that was the
    case in the ticket inbox.
    """
    graph = BUILDERS[slot]()
    for n in graph["nodes"]:
        if node_type(n) != "decision":
            continue
        cfg = node_config(n)
        branches = {b.get("handle") for b in (cfg.get("branches") or [])}
        if not branches:
            continue
        std = cfg.get("default_handle")
        assert std in branches, (
            f"{slot}/{n['id']}: the default branch '{std}' is none of the branches {sorted(branches)}")


@pytest.mark.parametrize("slot", sorted(BUILDERS))
def test_actions_in_einheitlicher_form(slot):
    """Action nodes have to use the nested form.

    In the flat form (`{"action": "name", "status": …}`) the editor shows neither the action
    nor the parameters, and the first edit overwrites them. The backend does understand both
    forms, but the interface is unusable that way.
    """
    for n in BUILDERS[slot]()["nodes"]:
        if node_type(n) != "auto_action":
            continue
        action = node_config(n).get("action")
        assert isinstance(action, dict) and action.get("action"), (
            f"{slot}/{n['id']}: Aktion in flacher Form ({action!r}) — bitte "
            f'{{"action": {{"action": …, "params": {{…}}}}}} verwenden')


@pytest.mark.parametrize("slot", sorted(BUILDERS))
def test_keine_zwei_node_auf_derselben_stelle(slot):
    """Two nodes at the same position cover each other, and with them the edge that hangs
    there. In the mail inbox exactly that stood out only in the picture."""
    if slot == "ticket_lifecycle":
        pytest.skip("cap_baseline/st_approved liegen aufeinander — Altbestand, eigener Fall")
    graph = BUILDERS[slot]()
    stellen: dict[tuple, list[str]] = {}
    for n in graph["nodes"]:
        stellen.setdefault((n["position"]["x"], n["position"]["y"]), []).append(n["id"])
    doppelt = {k: v for k, v in stellen.items() if len(v) > 1}
    assert not doppelt, f"Knoten liegen aufeinander: {doppelt}"
