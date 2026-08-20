"""Abläufe sprechen englisch — und verstehen weiter, was auf Deutsch geschrieben wurde.

Die Namen stehen in gespeicherten Fassungen, und veröffentlichte sind unveränderlich,
solange Instanzen an ihnen hängen. Deshalb zwei Wege: übersetzen beim Ausführen und einmal
umschreiben. Geprüft wird beides — vor allem, dass das Umschreiben genau EINMAL geschieht:
`assistant_task` hieß früher der Mail-Eingang und heute der allgemeine Auftrag.
"""
import pytest
from app.services import workflow_terms as terms



def _graph(aktion: str, params: dict, guard: dict | None = None) -> dict:
    knoten = [{"id": "a", "type": "auto_action",
               "data": {"config": {"action": {"action": aktion, "params": params}}}}]
    if guard is not None:
        knoten.append({"id": "w", "type": "decision",
                       "data": {"config": {"branches": [{"handle": "x", "guard": guard}]}}})
    return {"nodes": knoten, "edges": []}


def test_beim_ausfuehren_wird_uebersetzt():
    """Ein alter Graph läuft weiter, ohne dass er angefasst wurde."""
    assert terms.normalisiere_aktion("messwert") == "metric_record"
    assert terms.normalisiere_params({"reihe": "akku", "wert": 25}) == {"series": "akku", "value": 25}
    # Was schon englisch ist, bleibt.
    assert terms.normalisiere_params({"series": "akku"}) == {"series": "akku"}


def test_pfade_und_filter_werden_mitgenommen():
    text = "Noch {{ messreihe.rest_tage }} Tage · {{ quellen | verbinde:\", \" }} · {{ zeitfenster }}"
    assert terms._pfade_ersetzen(text) == (
        "Noch {{ metric.days_left }} Tage · {{ quellen | join:\", \" }} · {{ window }}")


def test_ein_wort_ohne_punkt_gehoert_dem_menschen():
    """`{{ titel }}` ist ein Job-Parameter, kein Kontextname — der bleibt."""
    assert terms._pfade_ersetzen("{{ titel }} / {{ thema }}") == "{{ titel }} / {{ thema }}"


def test_graph_wird_umgeschrieben():
    graph = _graph("messwert", {"reihe": "akku", "wert": "{{ payload.state }}",
                                "context_key": "messreihe"},
                   guard={"<=": [{"var": "messreihe.rest_tage"}, 3]})
    neu, anders = terms.migriere_graph(graph)
    assert anders
    aktion = neu["nodes"][0]["data"]["config"]["action"]
    assert aktion["action"] == "metric_record"
    assert aktion["params"] == {"series": "akku", "value": "{{ payload.state }}",
                                "context_key": "metric"}
    assert neu["nodes"][1]["data"]["config"]["branches"][0]["guard"] == {
        "<=": [{"var": "metric.days_left"}, 3]}


def test_zweimal_umschreiben_dreht_die_namen_nicht_weiter():
    """`assistant_task` hieß früher der Mail-Eingang und heute der allgemeine Auftrag —
    ein zweiter Durchgang über das eigene Ergebnis würde ihn zurückbiegen."""
    alt = _graph("assistent_auftrag", {"auftrag": "Mach was"})
    einmal, _ = terms.migriere_graph(alt)
    assert einmal["nodes"][0]["data"]["config"]["action"]["action"] == "assistant_task"

    zweimal, anders = terms.migriere_graph(einmal)
    assert anders is False
    assert zweimal["nodes"][0]["data"]["config"]["action"]["action"] == "assistant_task"


def test_der_alte_mail_weg_behaelt_seine_bedeutung():
    """In einem alten Graphen meinte `assistant_task` den Mail-Eingang."""
    neu, _ = terms.migriere_graph(_graph("assistant_task", {}))
    assert neu["nodes"][0]["data"]["config"]["action"]["action"] == "mail_assistant_task"


def test_ein_nachlauf_darf_neue_woerter_nachziehen():
    """Kommt später ein Wort dazu, wird noch einmal gelesen — aber ohne die Namen, die
    ihre Bedeutung gewechselt haben."""
    schon = _graph("assistant_task", {"task": "x", "titel": "Alt"})
    schon[terms.MARKE] = "en"          # eine ältere Marke: der erste Durchgang war schon
    neu, anders = terms.migriere_graph(schon)
    assert anders
    aktion = neu["nodes"][0]["data"]["config"]["action"]
    assert aktion["action"] == "assistant_task", "kein zweites Umbiegen"
    assert aktion["params"] == {"task": "x", "title": "Alt"}
