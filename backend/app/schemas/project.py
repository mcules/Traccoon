import datetime as dt

from pydantic import BaseModel, Field, field_validator

from ..models.enums import ProjectRole
from .auth import _valid_email


class ProjectCreate(BaseModel):
    # Key wird serverseitig aus dem Namen generiert (global eindeutig) — kein Eingabefeld mehr.
    name: str = Field(min_length=1, max_length=255)
    description: str = ""
    parent_id: int | None = None
    managed: bool = False


class ProjectUpdate(BaseModel):
    name: str | None = None
    description: str | None = None
    parent_id: int | None = None
    managed: bool | None = None
    pm_chat_enabled: bool | None = None
    verify_command: str | None = None
    review_enabled: bool | None = None
    auto_deploy: bool | None = None
    plan_agent: str | None = None
    exec_agent: str | None = None
    vault_moc_path: str | None = None


class ProjectSettings(BaseModel):
    """Agenten-/Git-/Deploy-Konfiguration eines Projekts (ohne Geheimnisse)."""
    managed: bool | None = None
    has_hardware: bool | None = None
    pm_chat_enabled: bool | None = None
    verify_command: str | None = None
    review_enabled: bool | None = None
    auto_continue: bool | None = None
    auto_deploy: bool | None = None
    screenshot_enabled: bool | None = None
    plan_agent: str | None = None
    exec_agent: str | None = None
    # Standard-Subscription des Projekts (überschreibt den persönlichen Default)
    default_provider: str | None = None
    default_token_name: str | None = None
    vault_moc_path: str | None = None
    system_prompt: str | None = None
    workspace_dir: str | None = None
    # Git
    git_enabled: bool | None = None
    github_repo: str | None = None
    work_in_branches: bool | None = None
    merge_target: str | None = None
    push_after_merge: bool | None = None
    use_pull_request: bool | None = None
    # Testumgebung
    testenv_mode: str | None = None
    testenv_container_port: int | None = None
    testenv_prestart: str | None = None
    testenv_demo_login: str | None = None


class ProjectSettingsOut(ProjectSettings):
    git_token_set: bool = False
    testenv_env_set: bool = False


class ProjectOut(BaseModel):
    id: int
    key: str
    name: str
    description: str
    parent_id: int | None
    avatar_color: str
    managed: bool
    pm_chat_enabled: bool
    has_hardware: bool
    git_enabled: bool = False
    # Sicht des aktuellen Nutzers auf dieses Projekt
    my_role: ProjectRole
    my_ai_assign: bool
    is_member: bool = True     # False = fremdes Projekt (nur Admin sieht das)
    is_new: bool = False       # kürzlich (≤7 Tage) hinzugefügtes Mitglied

    model_config = {"from_attributes": True}


class MemberCreate(BaseModel):
    user_id: int
    role: ProjectRole = ProjectRole.member
    ai_assign: bool | None = None  # None = rollenbasierte Vorbelegung


class MemberUpdate(BaseModel):
    role: ProjectRole | None = None
    ai_assign: bool | None = None


class MemberOut(BaseModel):
    id: int
    user_id: int
    username: str
    display_name: str
    role: ProjectRole
    ai_assign: bool

    model_config = {"from_attributes": True}


# ---------- Einladungen ----------

class InvitationCreate(BaseModel):
    email: str = Field(max_length=320)
    role: ProjectRole = ProjectRole.member

    _norm_email = field_validator("email")(_valid_email)


class InvitationOut(BaseModel):
    id: int
    project_id: int
    email: str
    role: ProjectRole
    status: str
    created_at: dt.datetime
    expires_at: dt.datetime | None

    model_config = {"from_attributes": True}


class InvitationPreview(BaseModel):
    """Was der Einladungslink vor dem Login/Register anzeigt."""
    project_key: str
    project_name: str
    email: str
    role: ProjectRole
    valid: bool
    reason: str | None = None
