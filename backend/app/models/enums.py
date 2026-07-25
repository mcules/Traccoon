import enum


class GlobalRole(str, enum.Enum):
    admin = "admin"
    user = "user"


class UserStatus(str, enum.Enum):
    pending = "pending"
    active = "active"
    disabled = "disabled"
    placeholder = "placeholder"  # Person ohne echtes Konto — nur als Zuweisungsziel nutzbar, kein Login


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
# Generische, deklarative Prozess-Engine (visueller Node-Graph). Läuft NEBEN dem
# KI-Ticket-Lifecycle (TicketAgentStatus) — siehe services/workflow_engine.py.

class WorkflowSubjectKind(str, enum.Enum):
    issue = "issue"                  # Instanz an ein Ticket gebunden (Voraussetzung für agent_task)
    hardware_asset = "hardware_asset"  # Instanz an ein Hardware-Exemplar gebunden
    standalone = "standalone"        # freistehend (kein Subject)


class WorkflowVersionStatus(str, enum.Enum):
    draft = "draft"          # editierbar
    published = "published"  # unveränderlich (Instanzen pinnen darauf)
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


class WorkflowTokenState(str, enum.Enum):
    active = "active"        # steht auf einem Knoten, bereit zum Weiterschalten
    waiting = "waiting"      # wartet auf externes Ereignis
    consumed = "consumed"    # aufgebraucht (End erreicht / Zweig beendet)


class WorkflowStepStatus(str, enum.Enum):
    pending = "pending"
    running = "running"
    waiting = "waiting"
    done = "done"
    failed = "failed"
    skipped = "skipped"


def pg_enum_values(e):
    """values_callable-Helfer: speichert die .value-Strings (lowercase) in PG."""
    return [member.value for member in e]
