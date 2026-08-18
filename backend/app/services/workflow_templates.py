"""Fertige Abläufe zum Kopieren.

Ein frischer Ablauf besteht aus Start und Ende — richtig so, aber es beantwortet die
Frage nicht, die davor steht: *wie* baut man damit etwas, das wirklich läuft? Die vier
Vorlagen hier sind keine Beispiele zum Anschauen, sondern Startpunkte: sie werden als
Version 1 angelegt, sind sofort probelauffähig und tragen an jedem Knoten die Stelle,
an der man den eigenen Fall einsetzt (Werkzeug, Ziel, Empfänger).

Sie decken die vier Muster ab, aus denen fast jeder eigene Ablauf besteht:

    außen → prüfen → melden          Meldung von außen verarbeiten
    Uhr  → holen → Freigabe → tun    geplante Prüfung mit Freigabe
    holen → Liste → je Element       Liste abarbeiten
    tun  → Fehler → warten → nochmal Aufruf mit Wiederholung

Gehalten wird das bewusst hier und nicht in der Datenbank: eine Vorlage ist eine
ausgelieferte Sache wie der Standard-Satz, keine Nutzerdatei. Wer sie ändern will,
legt sie an und baut sie um — die Kopie gehört ihm.
"""
from __future__ import annotations

from ..models.enums import WorkflowSubjectKind

_COL, _ROW = 260, 130


def _n(node_id: str, ntype: str, col: int, row: int, config: dict) -> dict:
    return {"id": node_id, "type": ntype,
            "position": {"x": col * _COL, "y": row * _ROW},
            "data": {"config": config}}


def _e(source: str, target: str, handle: str | None = None, label: str = "") -> dict:
    edge = {"id": f"e-{source}-{handle or 'out'}-{target}", "source": source, "target": target}
    if handle:
        edge["sourceHandle"] = handle
    if label:
        edge["label"] = label
    return edge


def _action(name: str, label: str, **cfg) -> dict:
    """auto_action-Konfiguration in der Form, die auch der Editor schreibt."""
    params = {k: v for k, v in cfg.items() if k not in ("wiederholungen", "warte_sek")}
    rest = {k: v for k, v in cfg.items() if k in ("wiederholungen", "warte_sek")}
    return {"label": label, "action": {"action": name, "params": params}, **rest}


def _ende(node_id: str, col: int, row: int, label: str, outcome: str = "completed") -> dict:
    return _n(node_id, "end", col, row, {"label": label, "outcome": outcome})


# ── 1) Meldung von außen ─────────────────────────────────────────────────────

def _meldung_von_aussen() -> dict:
    """Webhook rein, Weiche, Nachricht raus.

    Die Beispiel-Nutzlast am Start ist mehr als Zierde: aus ihr baut der Editor die
    Kontextfelder, die in der Weiche zur Auswahl stehen. Ohne sie stünde man an der
    Verzweigung vor einem leeren Dropdown.
    """
    nodes = [
        _n("start", "start", 0, 0, {
            "label": "Meldung von außen",
            "trigger": {"kind": "webhook",
                        "sample": {"titel": "Platte fast voll", "schwere": "hoch",
                                   "quelle": "monitoring"}}}),
        _n("weiche", "decision", 0, 1, {
            "label": "Dringend?",
            "branches": [
                {"handle": "dringend", "label": "ja",
                 "guard": {"==": [{"var": "schwere"}, "hoch"]}},
                {"handle": "egal", "label": "nein"},
            ],
            "default_handle": "egal"}),
        _n("melden", "auto_action", 0, 2, _action(
            "notify", "Bescheid geben",
            title="{{ titel }}",
            text="Von {{ quelle | default:extern }} gemeldet: {{ titel }}")),
        _ende("end_ok", 0, 3, "Gemeldet"),
        _ende("end_egal", 1, 2, "Nichts zu tun"),
    ]
    return {"nodes": nodes, "edges": [
        _e("start", "weiche"),
        _e("weiche", "melden", "dringend", "dringend"),
        _e("weiche", "end_egal", "egal", "kann warten"),
        _e("melden", "end_ok"),
    ]}


# ── 2) Geplante Prüfung mit Freigabe ─────────────────────────────────────────

def _pruefung_mit_freigabe() -> dict:
    """Etwas holen, hinsehen, und nur nach Freigabe handeln.

    Der Ablauf startet von Hand oder über einen Job (Einstellungen → Jobs, Art
    `workflow`) — deshalb kein Auslöser-Ereignis. Die Freigabe steht bewusst *vor*
    der wirksamen Aktion: was ein Automat nachts allein tut, will man morgens nicht
    erklären müssen.
    """
    nodes = [
        _n("start", "start", 0, 0, {"label": "Geplanter Lauf"}),
        _n("holen", "auto_action", 0, 1, _action(
            "tool_call", "Daten holen",
            tool="", arguments={}, context_key="tool")),
        _n("auffaellig", "decision", 0, 2, {
            "label": "Auffällig?",
            "branches": [
                {"handle": "ja", "label": "ja", "guard": {"==": [{"var": "tool.ok"}, True]}},
                {"handle": "nein", "label": "nein"},
            ],
            "default_handle": "nein"}),
        _n("freigabe", "approval", 0, 3, {
            "label": "Freigabe einholen",
            "gate": "none",
            "reason_required_on_reject": True}),
        _n("handeln", "auto_action", 0, 4, _action(
            "notify", "Handeln",
            title="Freigegeben — ausgeführt",
            text="Ergebnis: {{ tool.text | kurz:300 }}")),
        _ende("end_ok", 0, 5, "Erledigt"),
        _ende("end_nein", 1, 3, "Nichts Auffälliges"),
        _ende("end_abgelehnt", 2, 4, "Abgelehnt"),
    ]
    return {"nodes": nodes, "edges": [
        _e("start", "holen"),
        _e("holen", "auffaellig"),
        _e("auffaellig", "freigabe", "ja", "auffällig"),
        _e("auffaellig", "end_nein", "nein", "alles ruhig"),
        _e("freigabe", "handeln", "approved", "freigegeben"),
        _e("freigabe", "end_abgelehnt", "rejected", "abgelehnt"),
        _e("handeln", "end_ok"),
    ]}


# ── 3) Liste abarbeiten ──────────────────────────────────────────────────────

def _liste_abarbeiten() -> dict:
    """Eine Liste holen und Element für Element etwas damit tun.

    Die Rückkante vom Körper zur Schleife ist der ganze Trick — sie macht aus einem
    geraden Ablauf einen Durchlauf. `liste` zeigt auf den Pfad im Kontext, den der
    vorige Schritt gefüllt hat; was je Element geschieht, steht im Körper.
    """
    nodes = [
        _n("start", "start", 0, 0, {
            "label": "Start",
            "trigger": {"kind": "webhook",
                        "sample": {"posten": [{"name": "A"}, {"name": "B"}]}}}),
        _n("holen", "auto_action", 0, 1, _action(
            "tool_call", "Liste holen",
            tool="", arguments={}, context_key="tool")),
        _n("schleife", "loop", 0, 2, {
            "label": "Für jedes Element",
            "liste": "posten", "element": "element", "index": "i",
            "sammle": "schritt.action", "ergebnisse": "ergebnisse"}),
        _n("schritt", "auto_action", 1, 3, _action(
            "notify", "Element verarbeiten",
            title="Element {{ i }}",
            text="{{ element }}")),
        _n("bericht", "auto_action", 0, 4, _action(
            "notify", "Zusammenfassung",
            title="Durchlauf fertig",
            text="{{ ergebnisse | anzahl }} Elemente verarbeitet")),
        _ende("end_ok", 0, 5, "Fertig"),
    ]
    return {"nodes": nodes, "edges": [
        _e("start", "holen"),
        _e("holen", "schleife"),
        _e("schleife", "schritt", "element", "je Element"),
        _e("schritt", "schleife", None, "nächstes"),
        _e("schleife", "bericht", "fertig", "durch"),
        _e("bericht", "end_ok"),
    ]}


# ── 4) Aufruf mit Wiederholung ───────────────────────────────────────────────

def _aufruf_mit_wiederholung() -> dict:
    """Nach außen rufen, und bei Ärger nicht sofort aufgeben.

    Zwei Netze übereinander: der Knoten wiederholt sich dreimal von selbst (mit
    Abstand — sofort noch einmal wäre dieselbe Sekunde, derselbe Fehler), und erst
    wenn auch das nicht hilft, geht es über den roten Ausgang weiter. Dort wird
    gewartet und ein letztes Mal versucht, bevor jemand Bescheid bekommt.
    """
    nodes = [
        _n("start", "start", 0, 0, {"label": "Start"}),
        _n("rufen", "auto_action", 0, 1, _action(
            "http_request", "Ziel aufrufen",
            destination="", method="POST", path="/", fail_on_error=True,
            wiederholungen=3, warte_sek=60)),
        _ende("end_ok", 0, 2, "Durch"),
        _n("warten", "timer", 1, 2, {"label": "Später erneut", "dauer": 30, "einheit": "m"}),
        _n("nochmal", "auto_action", 1, 3, _action(
            "http_request", "Letzter Versuch",
            destination="", method="POST", path="/", fail_on_error=True)),
        _n("aufgeben", "auto_action", 2, 4, _action(
            "notify", "Bescheid geben",
            title="Aufruf endgültig fehlgeschlagen",
            text="Auch der Nachzügler kam nicht durch.")),
        _ende("end_fail", 2, 5, "Fehlgeschlagen", outcome="failed"),
    ]
    return {"nodes": nodes, "edges": [
        _e("start", "rufen"),
        _e("rufen", "end_ok", None, "durch"),
        _e("rufen", "warten", "error", "Fehler"),
        _e("warten", "nochmal"),
        _e("nochmal", "end_ok", None, "doch noch"),
        _e("nochmal", "aufgeben", "error", "auch das nicht"),
        _e("aufgeben", "end_fail"),
    ]}


VORLAGEN: list[dict] = [
    {"key": "meldung-von-aussen",
     "name": "Meldung von außen verarbeiten",
     "description": "Webhook rein, Weiche nach Dringlichkeit, Nachricht raus.",
     "subject_kind": WorkflowSubjectKind.standalone,
     "hinweis": "Beispiel-Nutzlast am Start anpassen — daraus entstehen die Kontextfelder.",
     "build": _meldung_von_aussen},
    {"key": "pruefung-mit-freigabe",
     "name": "Geplante Prüfung mit Freigabe",
     "description": "Daten holen, hinsehen, und erst nach Freigabe handeln.",
     "subject_kind": WorkflowSubjectKind.standalone,
     "hinweis": "Werkzeug im Schritt „Daten holen\" wählen; Start über einen Job.",
     "build": _pruefung_mit_freigabe},
    {"key": "liste-abarbeiten",
     "name": "Liste Element für Element abarbeiten",
     "description": "Liste holen, durchlaufen, je Element etwas tun, am Ende berichten.",
     "subject_kind": WorkflowSubjectKind.standalone,
     "hinweis": "In der Schleife den Pfad zur Liste eintragen (z. B. tool.json.items).",
     "build": _liste_abarbeiten},
    {"key": "aufruf-mit-wiederholung",
     "name": "Aufruf mit Wiederholung",
     "description": "Ziel aufrufen, bei Fehler wiederholen, später noch einmal, dann melden.",
     "subject_kind": WorkflowSubjectKind.standalone,
     "hinweis": "Ziel eintragen (Einstellungen → Ziele) — Basis-URL und Anmeldung stecken dort.",
     "build": _aufruf_mit_wiederholung},
]

_NACH_KEY = {v["key"]: v for v in VORLAGEN}


def liste() -> list[dict]:
    """Was zur Auswahl steht — ohne die Graphen selbst (die Übersicht braucht sie nicht)."""
    return [{k: (v.value if hasattr(v, "value") else v)
             for k, v in vorlage.items() if k != "build"}
            for vorlage in VORLAGEN]


def graph(key: str) -> dict | None:
    """Der Graph einer Vorlage — frisch gebaut, damit ihn niemand versehentlich teilt."""
    vorlage = _NACH_KEY.get(key)
    return vorlage["build"]() if vorlage else None


def vorlage(key: str) -> dict | None:
    return _NACH_KEY.get(key)
