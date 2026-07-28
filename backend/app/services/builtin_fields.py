"""Die eingebauten Felder von Ticket und Hardware-Exemplar.

Ticket und Exemplar haben ihre Daten seit jeher in echten Spalten — Priorität, Vorgangsart,
Sprint, Seriennummer, Kosten. Im Register tauchten sie nicht auf, weshalb die Frage „welche
Felder hat ein Ticket?" zwei Antworten an zwei Orten hatte.

Hier steht die eine Antwort. Jedes dieser Felder verhält sich im Register wie ein frei
angelegtes, schreibt seinen Wert aber weiter in die gewachsene Spalte — Board, Sprints und
der KI-Lebenszyklus lesen unverändert dort. `source` nennt die Spalte, `builtin=True` sperrt
Schlüssel, Typ und Löschen.

**Auch der Zustand ist nur ein Feld** (`status`). Ein zweites Zustands-Modell gibt es nicht
mehr; dass Engine und Board ein Feld namens `status` erwarten, trägt der gesperrte Schlüssel.

Bewusst NICHT aufgenommen: Maschinenzustand, den niemand von Hand pflegt — Merge-Ergebnis,
Testumgebung, Branch, Fortsetzungszähler, Plan-Text, Cap-Fenster. Er gehört dem Ablauf, nicht
dem Formular.
"""
from __future__ import annotations

from ..models.enums import Priority, PurchaseStatus, TicketAgentStatus

# (Wert, Beschriftung, Kategorie, wartet-auf-Menschen)
_TICKET_STATUS: list[tuple[str, str, str, bool]] = [
    (TicketAgentStatus.open.value, "Offen", "todo", False),
    (TicketAgentStatus.planning.value, "Planung läuft", "in_progress", False),
    (TicketAgentStatus.plan_review.value, "Plan wartet auf Freigabe", "in_progress", True),
    (TicketAgentStatus.approved.value, "Freigegeben", "in_progress", False),
    (TicketAgentStatus.in_progress.value, "In Umsetzung", "in_progress", False),
    (TicketAgentStatus.to_test.value, "Zur Abnahme bereit", "in_progress", True),
    (TicketAgentStatus.testing.value, "In Abnahme", "in_progress", True),
    (TicketAgentStatus.hold.value, "Angehalten", "in_progress", True),
    (TicketAgentStatus.failed.value, "Fehlgeschlagen", "in_progress", True),
    (TicketAgentStatus.done.value, "Fertig", "done", False),
]

_HARDWARE_STATUS: list[tuple[str, str, str, bool]] = [
    (PurchaseStatus.planned.value, "Geplant", "todo", False),
    (PurchaseStatus.ordered.value, "Bestellt", "in_progress", False),
    (PurchaseStatus.delivered.value, "Erhalten", "in_progress", False),
    (PurchaseStatus.stored.value, "Eingelagert", "in_progress", False),
    (PurchaseStatus.installed.value, "Eingebaut", "done", False),
    (PurchaseStatus.retired.value, "Ausgemustert", "done", False),
]

_PRIORITAET = [
    (Priority.lowest.value, "Sehr niedrig"), (Priority.low.value, "Niedrig"),
    (Priority.medium.value, "Mittel"), (Priority.high.value, "Hoch"),
    (Priority.highest.value, "Sehr hoch"),
]


def _f(key, label, kind, source, **rest) -> dict:
    return {"key": key, "label": label, "kind": kind, "source": source,
            "options": [], "options_source": "", "multi": False, **rest}


# Schlüssel des Artefakt-Typs → seine eingebauten Felder, in Anzeigereihenfolge.
BUILTIN_FIELDS: dict[str, list[dict]] = {
    "ticket": [
        _f("status", "Status", "select", "agent_status", options=_TICKET_STATUS),
        _f("vorgangsart", "Vorgangsart", "select", "type_id", options_source="issue_type"),
        _f("board", "Board-Spalte", "select", "status_id", options_source="board_status"),
        _f("prioritaet", "Priorität", "select", "priority",
           options=[(w, l, "", False) for w, l in _PRIORITAET]),
        _f("zustaendig", "Zuständig", "select", "assignee_user_id", options_source="member"),
        _f("sprint", "Sprint", "select", "sprint_id", options_source="sprint"),
        _f("story_points", "Story Points", "number", "story_points"),
        _f("faellig", "Fällig am", "date", "due_date"),
        _f("start", "Frühestens ab", "date", "start_at"),
        _f("nachts", "Nachtlauf erlaubt", "boolean", "night_task"),
    ],
    "hardware": [
        _f("status", "Status", "select", "purchase_status", options=_HARDWARE_STATUS),
        _f("seriennummer", "Seriennummer", "text", "serial_number"),
        _f("hersteller", "Lieferant", "text", "vendor"),
        _f("kosten", "Kosten", "number", "cost"),
        _f("standort", "Standort", "select", "location_id", options_source="location"),
        _f("bestellt_am", "Bestellt am", "date", "order_date"),
        _f("geliefert_am", "Geliefert am", "date", "delivery_date"),
        _f("eingebaut_am", "Eingebaut am", "date", "install_date"),
        _f("garantie_bis", "Garantie bis", "date", "warranty_until"),
        _f("notizen", "Notizen", "text", "notes"),
    ],
}

# Der Schlüssel des Zustands-Feldes. Board-Spiegel, Lebenszyklus und die Auswertung
# „wartet auf einen Menschen" lesen genau dieses Feld.
STATUS_KEY = "status"

# Spalte, in der der Zustand je Artefakt-Art steckt.
STATUS_SOURCE = {"ticket": "agent_status", "hardware": "purchase_status"}
