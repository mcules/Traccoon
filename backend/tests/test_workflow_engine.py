"""Unit tests for the JSONLogic subset plus graph validation and traversal of the workflow engine.

Runs with pytest OR standalone:  python tests/test_workflow_engine.py
(deliberately without database or async dependencies, only pure functions.)
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.services.jsonlogic import (  # noqa: E402
    JsonLogicError, collect_operators, evaluate, safe_eval,
)
from app.services.workflow_engine import next_node, validate_graph  # noqa: E402


# ── JSONLogic ────────────────────────────────────────────────────────────────

def test_var_and_equality():
    data = {"priority": "high", "count": 3, "nested": {"ok": True}}
    assert evaluate({"var": "priority"}, data) == "high"
    assert evaluate({"var": "nested.ok"}, data) is True
    assert safe_eval({"==": [{"var": "priority"}, "high"]}, data) is True
    assert safe_eval({"==": [{"var": "count"}, 3]}, data) is True
    assert safe_eval({"==": [{"var": "count"}, "3"]}, data) is True  # loose numeric


def test_comparisons_and_logic():
    data = {"count": 5, "flag": True}
    assert safe_eval({">": [{"var": "count"}, 3]}, data) is True
    assert safe_eval({"<=": [{"var": "count"}, 5]}, data) is True
    assert safe_eval({"and": [{">": [{"var": "count"}, 1]}, {"var": "flag"}]}, data) is True
    assert safe_eval({"or": [{"<": [{"var": "count"}, 1]}, {"var": "flag"}]}, data) is True
    assert safe_eval({"!": {"var": "flag"}}, data) is False
    assert safe_eval({"in": ["a", ["a", "b"]]}, data) is True


def test_arithmetic_and_missing_var_default():
    data = {"a": 2, "b": 4}
    assert evaluate({"+": [{"var": "a"}, {"var": "b"}]}, data) == 6
    assert evaluate({"*": [{"var": "a"}, 3]}, data) == 6
    assert evaluate({"var": ["missing", 99]}, data) == 99  # the default with a missing path


def test_disallowed_operator_raises():
    try:
        evaluate({"cat": ["a", "b"]}, {})
    except JsonLogicError:
        pass
    else:  # pragma: no cover
        raise AssertionError("the cat operator should have raised JsonLogicError")
    assert collect_operators({"and": [{"==": [{"var": "x"}, 1]}]}) == {"and", "==", "var"}


# ── Graph-Traversierung + Validierung ────────────────────────────────────────

def _linear_graph():
    return {
        "nodes": [
            {"id": "s", "type": "start"},
            {"id": "t", "type": "human_task", "data": {"config": {"label": "Tue was"}}},
            {"id": "e", "type": "end"},
        ],
        "edges": [
            {"id": "e1", "source": "s", "target": "t"},
            {"id": "e2", "source": "t", "target": "e", "sourceHandle": "out"},
        ],
    }


def test_next_node_default_and_named_handle():
    edges = _linear_graph()["edges"]
    assert next_node(edges, "s", "out") == "t"    # None sourceHandle ~ out
    assert next_node(edges, "t", "out") == "e"
    assert next_node(edges, "t", "nope") is None


def test_validate_ok_linear():
    assert validate_graph("standalone", _linear_graph()) == []


def test_validate_detects_missing_start_and_dangling():
    graph = {
        "nodes": [{"id": "t", "type": "human_task"}, {"id": "e", "type": "end"}],
        "edges": [{"id": "x", "source": "t", "target": "ghost"}],
    }
    errors = validate_graph("standalone", graph)
    assert any("Start-Knoten" in e for e in errors)
    assert any("unbekannt" in e for e in errors)


def test_validate_approval_requires_both_handles():
    graph = {
        "nodes": [
            {"id": "s", "type": "start"},
            {"id": "a", "type": "approval"},
            {"id": "e", "type": "end"},
        ],
        "edges": [
            {"id": "1", "source": "s", "target": "a"},
            {"id": "2", "source": "a", "target": "e", "sourceHandle": "approved"},
        ],
    }
    errors = validate_graph("standalone", graph)
    assert any("rejected" in e for e in errors)


def test_validate_agent_task_requires_issue_subject():
    graph = {
        "nodes": [
            {"id": "s", "type": "start"},
            {"id": "g", "type": "agent_task"},
            {"id": "e", "type": "end"},
        ],
        "edges": [
            {"id": "1", "source": "s", "target": "g"},
            {"id": "2", "source": "g", "target": "e", "sourceHandle": "out"},
        ],
    }
    assert any("subject_kind=issue" in e for e in validate_graph("standalone", graph))
    assert not any("subject_kind=issue" in e for e in validate_graph("issue", graph))


def test_validate_decision_handles_and_bad_operator():
    graph = {
        "nodes": [
            {"id": "s", "type": "start"},
            {"id": "d", "type": "decision", "data": {"config": {
                "branches": [{"handle": "yes", "guard": {"badop": [1, 2]}}],
                "default_handle": "no",
            }}},
            {"id": "e", "type": "end"},
        ],
        "edges": [
            {"id": "1", "source": "s", "target": "d"},
            {"id": "2", "source": "d", "target": "e", "sourceHandle": "yes"},
        ],
    }
    errors = validate_graph("standalone", graph)
    assert any("'no'" in e for e in errors)           # default-Handle-Kante fehlt
    assert any("badop" in e for e in errors)          # unbekannter Operator


def _run_all():
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    failed = 0
    for fn in fns:
        try:
            fn()
            print(f"PASS {fn.__name__}")
        except Exception as e:  # noqa: BLE001
            failed += 1
            print(f"FAIL {fn.__name__}: {e}")
    print(f"\n{len(fns) - failed}/{len(fns)} Tests bestanden")
    return failed


if __name__ == "__main__":
    sys.exit(1 if _run_all() else 0)
