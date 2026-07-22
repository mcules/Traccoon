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
    # Vererbung von Mitgliedschaften des Eltern-Baums abschalten (z. B. "Wart" soll NICHT
    # automatisch für jeden Freifunk-Owner sichtbar sein). Default an = gängiges Verhalten.
    inherit_members: Mapped[bool] = mapped_column(Boolean, default=True)
    avatar_color: Mapped[str] = mapped_column(String(20), default="#0052CC")
    lead_user_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )

    # Repo / Workspace
    workspace_dir: Mapped[str] = mapped_column(String(500), default="")
    github_repo: Mapped[str] = mapped_column(String(500), default="")
    git_token_enc: Mapped[str] = mapped_column(String, default="")
    git_enabled: Mapped[bool] = mapped_column(Boolean, default=False)
    work_in_branches: Mapped[bool] = mapped_column(Boolean, default=True)
    merge_target: Mapped[str] = mapped_column(String(255), default="main")
    # Statt direkt zu mergen: Branch pushen und einen Pull Request öffnen (GitHub).
    use_pull_request: Mapped[bool] = mapped_column(Boolean, default=False)
    # Testumgebungen: compose.preview.yml (Standard) oder Dockerfile-Build
    testenv_mode: Mapped[str] = mapped_column(String(20), default="compose")
    testenv_container_port: Mapped[int] = mapped_column(Integer, default=8080)  # nur dockerfile-Modus
    testenv_prestart: Mapped[str] = mapped_column(Text, default="")   # im Worktree vor dem Start
    testenv_env_enc: Mapped[str] = mapped_column(Text, default="")    # Fernet-JSON, Env für die Preview
    testenv_demo_login: Mapped[str] = mapped_column(Text, default="")  # JSON für den Screenshot-Login
    push_after_merge: Mapped[bool] = mapped_column(Boolean, default=True)
    auto_pull: Mapped[bool] = mapped_column(Boolean, default=True)
    pull_interval_min: Mapped[int] = mapped_column(Integer, default=15)

    # KI-Managed
    managed: Mapped[bool] = mapped_column(Boolean, default=False)
    has_hardware: Mapped[bool] = mapped_column(Boolean, default=False)  # Hardware-Tab ein/aus
    auto_create_agents: Mapped[bool] = mapped_column(Boolean, default=False)
    system_prompt: Mapped[str] = mapped_column(Text, default="")
    pm_chat_enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    plan_agent: Mapped[str] = mapped_column(String(100), default="architect")
    exec_agent: Mapped[str] = mapped_column(String(100), default="developer")
    # Standard-Subscription/Token dieses Projekts — überschreibt den persönlichen
    # Default-ProviderToken des Nutzers (greift, wenn ein Agent keinen eigenen Token wählt).
    default_provider: Mapped[str] = mapped_column(String(50), default="")
    default_token_name: Mapped[str] = mapped_column(String(120), default="")
    verify_command: Mapped[str] = mapped_column(Text, default="")
    review_enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    review_agent: Mapped[str] = mapped_column(String(100), default="code_reviewer")
    auto_continue: Mapped[bool] = mapped_column(Boolean, default=True)
    comment_triggers_agent: Mapped[bool] = mapped_column(Boolean, default=True)

    # Deploy / Predecessor
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
    # KI-Recht: darf PM-Chat nutzen + Tickets Agenten zuweisen (orthogonal zur Rolle)
    ai_assign: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)

    project: Mapped["Project"] = relationship(back_populates="members")


def default_ai_assign(role: ProjectRole) -> bool:
    """Rollenbasierte Vorbelegung des KI-Rechts (überschreibbar)."""
    return role in (ProjectRole.owner, ProjectRole.maintainer)


class ResourceGrant(TimestampMixin, Base):
    """Granulare Freigabe eines einzelnen Objekts (Location/Asset) an einen User,
    unabhängig von dessen Projekt-Rolle. Deckt den "Wart"-Fall: User sieht/verwaltet
    NUR das Wasserhäuschen + seine Masten, ohne volle Projekt-Mitgliedschaft.
    """
    __tablename__ = "resource_grants"
    __table_args__ = (
        UniqueConstraint(
            "user_id", "resource_type", "resource_id", name="uq_resource_grant"
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    # Projekt, in dessen Kontext die Freigabe gilt (Sichtbarkeit im Hardware-Tab etc.)
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
    # Gilt die Freigabe auch für Kind-Locations (Mast unterm Wasserhäuschen)?
    recursive: Mapped[bool] = mapped_column(Boolean, default=True)
