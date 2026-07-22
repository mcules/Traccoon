import enum


class GlobalRole(str, enum.Enum):
    admin = "admin"
    user = "user"


class UserStatus(str, enum.Enum):
    pending = "pending"
    active = "active"
    disabled = "disabled"


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


def pg_enum_values(e):
    """values_callable-Helfer: speichert die .value-Strings (lowercase) in PG."""
    return [member.value for member in e]
