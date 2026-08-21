"""Flows speak English — and still understand what was written in German.

The names stand in stored versions, and published ones are immutable as long as instances hang
on them. Hence two ways: translate at run time and rewrite once. Both are checked — above all
that the rewriting happens exactly ONCE: `assistant_task` used to mean the mail intake and
today means the general assignment.
"""
import pytest
from app.services import workflow_terms as terms



def _graph(action: str, params: dict, guard: dict | None = None) -> dict:
    node = [{"id": "a", "type": "auto_action",
               "data": {"config": {"action": {"action": action, "params": params}}}}]
    if guard is not None:
        node.append({"id": "w", "type": "decision",
                       "data": {"config": {"branches": [{"handle": "x", "guard": guard}]}}})
    return {"nodes": node, "edges": []}


def test_translation_happens_when_running():
    """An old graph keeps running without having been touched."""
    assert terms.normalise_action("messwert") == "metric_record"
    assert terms.normalise_params({"reihe": "akku", "wert": 25}) == {"series": "akku", "value": 25}
    # What is already English stays.
    assert terms.normalise_params({"series": "akku"}) == {"series": "akku"}


def test_paths_and_filters_are_carried_along():
    text = "Noch {{ messreihe.rest_tage }} Tage · {{ quellen | verbinde:\", \" }} · {{ zeitfenster }}"
    assert terms._paths_replace(text) == (
        "Noch {{ metric.days_left }} Tage · {{ quellen | join:\", \" }} · {{ window }}")


def test_a_word_without_a_dot_belongs_to_the_person():
    """`{{ titel }}` is a job parameter, not a context name — that one stays."""
    assert terms._paths_replace("{{ titel }} / {{ thema }}") == "{{ titel }} / {{ thema }}"


def test_the_graph_is_rewritten():
    graph = _graph("messwert", {"reihe": "akku", "wert": "{{ payload.state }}",
                                "context_key": "messreihe"},
                   guard={"<=": [{"var": "messreihe.rest_tage"}, 3]})
    new, different = terms.migrate_graph(graph)
    assert different
    action = new["nodes"][0]["data"]["config"]["action"]
    assert action["action"] == "metric_record"
    assert action["params"] == {"series": "akku", "value": "{{ payload.state }}",
                                "context_key": "metric"}
    assert new["nodes"][1]["data"]["config"]["branches"][0]["guard"] == {
        "<=": [{"var": "metric.days_left"}, 3]}


def test_rewriting_twice_does_not_turn_the_names_further():
    """`assistant_task` used to mean the mail intake and today the general assignment —
    a second pass over its own result would bend it back."""
    old = _graph("assistent_auftrag", {"auftrag": "Mach was"})
    once, _ = terms.migrate_graph(old)
    assert once["nodes"][0]["data"]["config"]["action"]["action"] == "assistant_task"

    twice, different = terms.migrate_graph(once)
    assert different is False
    assert twice["nodes"][0]["data"]["config"]["action"]["action"] == "assistant_task"


def test_the_old_mail_path_keeps_its_meaning():
    """In an old graph `assistant_task` meant the mail intake."""
    new, _ = terms.migrate_graph(_graph("assistant_task", {}))
    assert new["nodes"][0]["data"]["config"]["action"]["action"] == "mail_assistant_task"


def test_a_follow_up_may_pull_in_new_words():
    """If a word is added later, it is read again — but without the names that
    ihre Bedeutung gewechselt haben."""
    already = _graph("assistant_task", {"task": "x", "titel": "Alt"})
    already[terms.MARK] = "en"          # an older mark: the first pass had already happened
    new, different = terms.migrate_graph(already)
    assert different
    action = new["nodes"][0]["data"]["config"]["action"]
    assert action["action"] == "assistant_task", "kein zweites Umbiegen"
    assert action["params"] == {"task": "x", "title": "Alt"}
