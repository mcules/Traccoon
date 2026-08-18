"""The built-in fields of ticket and hardware unit.

Ticket and unit have always had their data in real columns: priority, issue type, sprint,
serial number, cost. They did not appear in the register, which is why the question "which
fields does a ticket have?" had two answers in two places.

Here stands the one answer. Every one of these fields behaves in the register like a freely
created one but keeps writing its value into the grown column; board, sprints and the AI
lifecycle read there unchanged. `source` names the column, `builtin=True` locks the key, the
type and deletion.

**The state is only a field as well** (`status`). There is no second state model any more;
that engine and board expect a field called `status` is carried by the locked key.

Deliberately NOT included: machine state nobody maintains by hand, so the merge result, the
test environment, the branch, the continuation counter, the plan text, the cap window. It
belongs to the flow, not to the form.
"""
from __future__ import annotations

from ..models.enums import Priority, PurchaseStatus, TicketAgentStatus

# (value, label, category, waits for a human)
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


# Key of the artifact type to its built-in fields, in display order.
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

# The key of the state field. Board mirror, lifecycle and the "waiting for a human" evaluation
# read exactly this field.
STATUS_KEY = "status"

# Column the state sits in per kind of artifact.
STATUS_SOURCE = {"ticket": "agent_status", "hardware": "purchase_status"}
