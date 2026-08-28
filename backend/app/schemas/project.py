import datetime as dt

from pydantic import BaseModel, Field, field_validator

from ..models.enums import GrantLevel, ProjectRole, ResourceType
from .auth import _valid_email


class ProjectCreate(BaseModel):
    # The key is generated server side from the name (globally unique); no longer an input field.
    name: str = Field(min_length=1, max_length=255)
    description: str = ""
    parent_id: int | None = None
    managed: bool = False


class ProjectUpdate(BaseModel):
    name: str | None = None
    description: str | None = None
    parent_id: int | None = None
    inherit_members: bool | None = None
    managed: bool | None = None
    pm_chat_enabled: bool | None = None
    verify_command: str | None = None
    review_enabled: bool | None = None
    auto_deploy: bool | None = None
    plan_agent: str | None = None
    exec_agent: str | None = None
    vault_moc_path: str | None = None


class ProjectSettings(BaseModel):
    """Agent, git and deploy configuration of a project (without secrets)."""
    # What the project is: the knowledge about it that outlives a single ticket. It sits in
    # the settings and not only in `ProjectUpdate`, because that is where it is written and
    # kept: the creation dialog asks for a name, the description grows afterwards. The agents
    # are given it on every run (worker/runtime._project_knowledge).
    description: str | None = None
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
    # Default subscription of the project (overrides the personal default)
    default_provider: str | None = None
    default_token_name: str | None = None
    vault_moc_path: str | None = None
    # ── Reports ─────────────────────────────────────────────────────────────
    # Which of one's own mailboxes carries the answers (login and server stay there), and
    # under which address they go out. Both empty: the reports of this project are answered
    # where they can be read, not by mail.
    mail_account_id: int | None = None
    reply_from: str | None = None
    reply_name: str | None = None
    # Which agent formulates a draft. Empty = the classifying agent of the mail intake. It
    # proposes; sending stays with the person, always.
    answer_agent: str | None = None
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
    testenv_enabled: bool | None = None
    testenv_mode: str | None = None
    testenv_compose_file: str | None = None
    testenv_dockerfile: str | None = None
    testenv_url_template: str | None = None
    testenv_container_port: int | None = None
    testenv_prestart: str | None = None
    testenv_demo_login: str | None = None


class ProjectSettingsOut(ProjectSettings):
    git_token_set: bool = False
    testenv_env_set: bool = False
    # The name of the chosen mailbox. Read only, so that the form can say which one it is
    # instead of showing a number.
    mail_account_name: str = ""


class ProjectOut(BaseModel):
    id: int
    key: str
    name: str
    description: str
    parent_id: int | None
    inherit_members: bool = True
    avatar_color: str
    managed: bool
    pm_chat_enabled: bool
    has_hardware: bool
    git_enabled: bool = False
    testenv_enabled: bool = True
    # View of the current user on this project
    my_role: ProjectRole
    my_ai_assign: bool
    is_member: bool = True     # False = a foreign project (only an admin sees that)
    is_new: bool = False       # recently (<= 7 days) added member
    # Was the role direct (membership of this project) or inherited from the parent tree?
    my_role_inherited: bool = False

    model_config = {"from_attributes": True}


class MemberCreate(BaseModel):
    user_id: int
    role: ProjectRole = ProjectRole.member
    ai_assign: bool | None = None  # None = rollenbasierte Vorbelegung


class MemberUpdate(BaseModel):
    role: ProjectRole | None = None
    ai_assign: bool | None = None


class AliasIn(BaseModel):
    """The name somebody gives themselves in one project. Empty puts the account name back."""
    alias: str = Field(default="", max_length=255)


class MemberOut(BaseModel):
    id: int
    user_id: int
    username: str
    # What this person is called in THIS project: the alias if they set one, otherwise their
    # account name. Everything that shows a name reads this field and needs to know nothing
    # about the three sources behind it.
    display_name: str
    # The raw alias, so the form can show what is set and what is merely inherited.
    alias: str = ""
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
    """What the invitation link shows before login or registration."""
    project_key: str
    project_name: str
    email: str
    role: ProjectRole
    valid: bool
    reason: str | None = None


# ---------- Resource-Grants (granulare Freigaben) ----------

class ResourceGrantIn(BaseModel):
    user_id: int
    resource_type: ResourceType
    resource_id: int
    level: GrantLevel = GrantLevel.view
    recursive: bool = True


class ResourceGrantOut(BaseModel):
    id: int
    project_id: int
    user_id: int
    username: str
    display_name: str
    resource_type: ResourceType
    resource_id: int
    resource_label: str
    level: GrantLevel
    recursive: bool

    model_config = {"from_attributes": True}
