import enum


class GlobalRole(str, enum.Enum):
    admin = "admin"
    user = "user"


class UserStatus(str, enum.Enum):
    pending = "pending"
    active = "active"
    disabled = "disabled"
    placeholder = "placeholder"  # person without a real account: usable only as an assignment target, no login


class ProjectRole(str, enum.Enum):
    owner = "owner"
    maintainer = "maintainer"
    member = "member"
    viewer = "viewer"


class Priority(str, enum.Enum):
    lowest = "lowest"
    low = "low"
    medium = "medium"
    high = "high"
    highest = "highest"


class StatusCategory(str, enum.Enum):
    todo = "todo"
    in_progress = "in_progress"
    done = "done"


class IssueTypeCategory(str, enum.Enum):
    standard = "standard"
    subtask = "subtask"
    epic = "epic"
    hardware = "hardware"


class TicketAgentStatus(str, enum.Enum):
    open = "open"
    planning = "planning"
    plan_review = "plan_review"
    approved = "approved"
    in_progress = "in_progress"
    testing = "testing"
    to_test = "to_test"
    done = "done"
    hold = "hold"
    failed = "failed"


class HoldReason(str, enum.Enum):
    plan_review = "plan_review"
    plan_split = "plan_split"
    question = "question"
    merge = "merge"
    verify = "verify"
    review = "review"
    incomplete = "incomplete"
    stuck = "stuck"
    cap = "cap"
    interrupted = "interrupted"
    permission = "permission"


class BoardType(str, enum.Enum):
    kanban = "kanban"
    scrum = "scrum"


class SprintState(str, enum.Enum):
    future = "future"
    active = "active"
    closed = "closed"


class LinkType(str, enum.Enum):
    blocks = "blocks"
    relates = "relates"
    duplicates = "duplicates"
    clones = "clones"


class LocationType(str, enum.Enum):
    room = "room"
    rack = "rack"
    shelf = "shelf"
    slot = "slot"
    server = "server"
    other = "other"


class PurchaseStatus(str, enum.Enum):
    planned = "planned"
    ordered = "ordered"
    delivered = "delivered"
    installed = "installed"
    stored = "stored"
    retired = "retired"


class ResourceType(str, enum.Enum):
    """Objektart einer granularen Freigabe (resource_grants)."""
    location = "location"
    asset = "asset"


class GrantLevel(str, enum.Enum):
    view = "view"
    manage = "manage"


# ── Workflow-Engine ──────────────────────────────────────────────────────────
# Generic, declarative process engine (visual node graph). Runs BESIDE the AI ticket
# lifecycle (TicketAgentStatus); see services/workflow_engine.py.

class WorkflowSubjectKind(str, enum.Enum):
    issue = "issue"                  # instance bound to a ticket (precondition for agent_task)
    hardware_asset = "hardware_asset"  # instance bound to a hardware unit
    standalone = "standalone"        # free standing (no subject)


class WorkflowVersionStatus(str, enum.Enum):
    draft = "draft"          # editierbar
    published = "published"  # immutable (instances pin to it)
    archived = "archived"


class WorkflowInstanceStatus(str, enum.Enum):
    running = "running"      # Token aktiv, schaltet weiter
    waiting = "waiting"      # Token wartet (Human-Task/Approval/Agent)
    completed = "completed"
    failed = "failed"
    cancelled = "cancelled"


class WorkflowNodeType(str, enum.Enum):
    start = "start"
    end = "end"
    human_task = "human_task"
    decision = "decision"
    approval = "approval"
    auto_action = "auto_action"
    agent_task = "agent_task"
    wait_event = "wait_event"  # waits for an external event (comment, answer, manual)
    subflow = "subflow"        # starts the definition of a slot as a child instance
    loop = "loop"              # walks through a list element by element
    timer = "timer"            # waits a while respectively until a point in time


class WorkflowSetScope(str, enum.Enum):
    """Geltungsbereich eines Prozess-Satzes.

    global_ = the one shipped default set (is_builtin) or a further system wide set
    (admin); user = a personal set, applying to all projects in which the user has the
    owner role.
    """
    global_ = "global"
    user = "user"


class WorkflowSlot(str, enum.Enum):
    """Firmly named flows Traccoon triggers itself. A set occupies at most one definition per
    slot; projects can override a slot with a copy of their own (copy-on-write) and reset it
    at any time."""
    ticket_lifecycle = "ticket_lifecycle"          # KI-Ticket-Lebenszyklus
    acceptance = "acceptance"                      # acceptance: clear the test env, merge, deploy
    hardware_procurement = "hardware_procurement"  # procurement of a unit
    ticket_intake = "ticket_intake"                # inbox (webhook, mail) to ticket
    # The mail inbox deliberately does NOT stand here any more: it is nobody's default but
    # one person's flow. It lives as the template `mail-eingang` (workflow_templates), is
    # created once and belongs to whoever created it — including every change to it.


class WorkflowTokenState(str, enum.Enum):
    active = "active"        # stands on a node, ready to advance
    waiting = "waiting"      # waits for an external event
    consumed = "consumed"    # aufgebraucht (End erreicht / Zweig beendet)


class WorkflowStepStatus(str, enum.Enum):
    pending = "pending"
    running = "running"
    waiting = "waiting"
    done = "done"
    failed = "failed"
    skipped = "skipped"


def pg_enum_values(e):
    """values_callable helper: stores the .value strings (lower case) in PG."""
    return [member.value for member in e]
