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
    (TicketAgentStatus.open.value, "Open", "todo", False),
    (TicketAgentStatus.planning.value, "Planning", "in_progress", False),
    (TicketAgentStatus.plan_review.value, "The plan waits for approval", "in_progress", True),
    (TicketAgentStatus.approved.value, "Approved", "in_progress", False),
    (TicketAgentStatus.in_progress.value, "In progress", "in_progress", False),
    (TicketAgentStatus.to_test.value, "Ready for acceptance", "in_progress", True),
    (TicketAgentStatus.testing.value, "In acceptance", "in_progress", True),
    (TicketAgentStatus.hold.value, "On hold", "in_progress", True),
    (TicketAgentStatus.failed.value, "Failed", "in_progress", True),
    (TicketAgentStatus.done.value, "Done", "done", False),
]

_HARDWARE_STATUS: list[tuple[str, str, str, bool]] = [
    (PurchaseStatus.planned.value, "Planned", "todo", False),
    (PurchaseStatus.ordered.value, "Ordered", "in_progress", False),
    (PurchaseStatus.delivered.value, "Delivered", "in_progress", False),
    (PurchaseStatus.stored.value, "Stored", "in_progress", False),
    (PurchaseStatus.installed.value, "Installed", "done", False),
    (PurchaseStatus.retired.value, "Retired", "done", False),
]

_PRIORITY = [
    (Priority.lowest.value, "Lowest"), (Priority.low.value, "Low"),
    (Priority.medium.value, "Medium"), (Priority.high.value, "High"),
    (Priority.highest.value, "Highest"),
]


def _f(key, label, kind, source, **remainder) -> dict:
    return {"key": key, "label": label, "kind": kind, "source": source,
            "options": [], "options_source": "", "multi": False, **remainder}


# Key of the artifact type to its built-in fields, in display order.
#
# The KEYS stay German. They stand in the field configuration of every project, in published
# flows (`set_field`) and in the layouts people arranged for themselves; renaming them is a
# data migration and not a translation. The labels above them are what one reads.
BUILTIN_FIELDS: dict[str, list[dict]] = {
    "ticket": [
        _f("status", "Status", "select", "agent_status", options=_TICKET_STATUS),
        _f("vorgangsart", "Kind of matter", "select", "type_id", options_source="issue_type"),
        _f("board", "Board column", "select", "status_id", options_source="board_status"),
        _f("prioritaet", "Priority", "select", "priority",
           options=[(w, l, "", False) for w, l in _PRIORITY]),
        _f("zustaendig", "Assignee", "select", "assignee_user_id", options_source="member"),
        _f("sprint", "Sprint", "select", "sprint_id", options_source="sprint"),
        _f("story_points", "Story Points", "number", "story_points"),
        _f("faellig", "Due on", "date", "due_date"),
        _f("start", "Not before", "date", "start_at"),
        _f("nachts", "A night run is allowed", "boolean", "night_task"),
    ],
    "hardware": [
        _f("status", "Status", "select", "purchase_status", options=_HARDWARE_STATUS),
        _f("seriennummer", "Serial number", "text", "serial_number"),
        _f("hersteller", "Supplier", "text", "vendor"),
        _f("kosten", "Cost", "number", "cost"),
        _f("standort", "Location", "select", "location_id", options_source="location"),
        _f("bestellt_am", "Ordered on", "date", "order_date"),
        _f("geliefert_am", "Delivered on", "date", "delivery_date"),
        _f("eingebaut_am", "Installed on", "date", "install_date"),
        _f("garantie_bis", "Warranty until", "date", "warranty_until"),
        _f("notizen", "Notes", "text", "notes"),
    ],
}

# The key of the state field. Board mirror, lifecycle and the "waiting for a human" evaluation
# read exactly this field.
STATUS_KEY = "status"

# Column the state sits in per kind of artifact.
STATUS_SOURCE = {"ticket": "agent_status", "hardware": "purchase_status"}
