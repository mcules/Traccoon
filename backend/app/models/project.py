from sqlalchemy import Boolean, Enum as SAEnum, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from ..db import Base
from .base import TimestampMixin
from .enums import GrantLevel, ProjectRole, ResourceType, pg_enum_values


class Project(TimestampMixin, Base):
    __tablename__ = "projects"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    key: Mapped[str] = mapped_column(String(30), unique=True, nullable=False)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    description: Mapped[str] = mapped_column(Text, default="")
    parent_id: Mapped[int | None] = mapped_column(
        ForeignKey("projects.id", ondelete="SET NULL"), nullable=True, index=True
    )
    # Switch off the inheritance of memberships from the parent tree (a caretaker project
    # should NOT be visible to every owner above automatically). On by default = usual behaviour.
    inherit_members: Mapped[bool] = mapped_column(Boolean, default=True)
    avatar_color: Mapped[str] = mapped_column(String(20), default="#0052CC")
    lead_user_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    # Process set of this project (a reference, not a copy). NULL = the set of an owner
    # respectively the global default set; see services/workflow_sets.resolve_definition.
    workflow_set_id: Mapped[int | None] = mapped_column(
        ForeignKey("workflow_sets.id", ondelete="SET NULL"), nullable=True
    )

    # Repo / Workspace
    workspace_dir: Mapped[str] = mapped_column(String(500), default="")
    github_repo: Mapped[str] = mapped_column(String(500), default="")
    git_token_enc: Mapped[str] = mapped_column(String, default="")
    git_enabled: Mapped[bool] = mapped_column(Boolean, default=False)
    work_in_branches: Mapped[bool] = mapped_column(Boolean, default=True)
    merge_target: Mapped[str] = mapped_column(String(255), default="main")
    # Instead of merging directly: push the branch and open a pull request (GitHub).
    use_pull_request: Mapped[bool] = mapped_column(Boolean, default=False)
    # Test environments: compose.preview.yml (default) or a Dockerfile build
    # On: a finished implementation lands on "testing" instead of being merged directly (ABC-18).
    testenv_enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    testenv_mode: Mapped[str] = mapped_column(String(20), default="compose")
    testenv_compose_file: Mapped[str] = mapped_column(String(255), default="compose.preview.yml")
    testenv_dockerfile: Mapped[str] = mapped_column(String(255), default="Dockerfile")
    testenv_url_template: Mapped[str] = mapped_column(String(255), default="http://{host}:{port}")
    testenv_container_port: Mapped[int] = mapped_column(Integer, default=8080)  # dockerfile mode only
    testenv_prestart: Mapped[str] = mapped_column(Text, default="")   # in the worktree before the start
    testenv_env_enc: Mapped[str] = mapped_column(Text, default="")    # Fernet JSON, env for the preview
    testenv_demo_login: Mapped[str] = mapped_column(Text, default="")  # JSON for the screenshot login
    push_after_merge: Mapped[bool] = mapped_column(Boolean, default=True)
    auto_pull: Mapped[bool] = mapped_column(Boolean, default=True)
    pull_interval_min: Mapped[int] = mapped_column(Integer, default=15)

    # KI-Managed
    managed: Mapped[bool] = mapped_column(Boolean, default=False)
    has_hardware: Mapped[bool] = mapped_column(Boolean, default=False)  # hardware tab on/off
    auto_create_agents: Mapped[bool] = mapped_column(Boolean, default=False)
    system_prompt: Mapped[str] = mapped_column(Text, default="")
    pm_chat_enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    plan_agent: Mapped[str] = mapped_column(String(100), default="architect")
    exec_agent: Mapped[str] = mapped_column(String(100), default="developer")
    # Default subscription or token of this project; overrides the personal default
    # ProviderToken of the user (takes hold when an agent chooses no token of its own).
    default_provider: Mapped[str] = mapped_column(String(50), default="")
    default_token_name: Mapped[str] = mapped_column(String(120), default="")
    verify_command: Mapped[str] = mapped_column(Text, default="")
    review_enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    review_agent: Mapped[str] = mapped_column(String(100), default="code_reviewer")
    auto_continue: Mapped[bool] = mapped_column(Boolean, default=True)
    comment_triggers_agent: Mapped[bool] = mapped_column(Boolean, default=True)

    # Deploy / Betrieb
    auto_deploy: Mapped[bool] = mapped_column(Boolean, default=False)
    screenshot_enabled: Mapped[bool] = mapped_column(Boolean, default=False)
    vault_moc_path: Mapped[str] = mapped_column(String(500), default="")

    members: Mapped[list["ProjectMember"]] = relationship(
        back_populates="project", cascade="all, delete-orphan"
    )


class ProjectMember(TimestampMixin, Base):
    __tablename__ = "project_members"
    __table_args__ = (UniqueConstraint("project_id", "user_id", name="uq_project_member"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    project_id: Mapped[int] = mapped_column(
        ForeignKey("projects.id", ondelete="CASCADE"), nullable=False, index=True
    )
    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    role: Mapped[ProjectRole] = mapped_column(
        SAEnum(ProjectRole, name="projectrole", values_callable=pg_enum_values),
        default=ProjectRole.member, nullable=False,
    )
    # AI right: may use the PM chat and assign agents to tickets (orthogonal to the role)
    ai_assign: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)

    project: Mapped["Project"] = relationship(back_populates="members")


def default_ai_assign(role: ProjectRole) -> bool:
    """Rollenbasierte Vorbelegung des KI-Rechts (überschreibbar)."""
    return role in (ProjectRole.owner, ProjectRole.maintainer)


class ResourceGrant(TimestampMixin, Base):
    """Granular grant of a single object (location, asset) to a user, independently of their
    project role. Covers the caretaker case: the user sees and manages ONLY the pump house
    plus its masts, without a full project membership.
    """
    __tablename__ = "resource_grants"
    __table_args__ = (
        UniqueConstraint(
            "user_id", "resource_type", "resource_id", name="uq_resource_grant"
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    # Project in whose context the grant applies (visibility in the hardware tab and so on)
    project_id: Mapped[int] = mapped_column(
        ForeignKey("projects.id", ondelete="CASCADE"), nullable=False, index=True
    )
    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    resource_type: Mapped[ResourceType] = mapped_column(
        SAEnum(ResourceType, name="resourcetype", values_callable=pg_enum_values), nullable=False,
    )
    resource_id: Mapped[int] = mapped_column(Integer, nullable=False)
    level: Mapped[GrantLevel] = mapped_column(
        SAEnum(GrantLevel, name="grantlevel", values_callable=pg_enum_values),
        default=GrantLevel.view, nullable=False,
    )
    # Does the grant apply to child locations as well (a mast below the pump house)?
    recursive: Mapped[bool] = mapped_column(Boolean, default=True)
