"""Artifacts: the common view of everything that has a state.

Ticket and hardware unit used to be two unconnected worlds with a status axis each
(`TicketAgentStatus`, `PurchaseStatus`), which in the process editor led to three different
status actions of which two were pointless depending on the flow.

Here stands the register: which **artifact types** exist and which **states** each of them
knows, maintainable in the administration. Where the data comes from is said by `backing`:

    issue           → table `issues` (state in `agent_status`)
    hardware_asset  → table `hardware_assets` (state in `purchase_status`)
    generic         → table `artifacts` (freely defined types)

That makes the type configurable without board, sprints or the AI lifecycle losing their
grown tables. New, self defined types land in `artifacts`.

Below it hangs the field model following the Artefakt example (`Artifacts → Fields → Values`):
an artifact carries any number of **fields**, a field of type "choice" a maintained **value
list**, and the field says whether a single artifact may carry one or several values from
it.

An artifact is initially something undefined; it gets its meaning only through its fields.
Ticket and hardware are therefore nothing special but artifacts with a shipped set of fixed
fields. A project may add any fields of its own; the shipped ones cannot be removed, because
board, sprints and the AI lifecycle run on them.

Terms: the interface speaks differently from the code, because renaming the tables would
drag the foreign keys from `issues`, `hardware_assets` and `workflow_instances` along:

    interface           code / table                            example
    artifact type       ArtifactType / artifact_types           ticket, hardware
    field               ArtifactField / artifact_fields         priority
    value (list)        ArtifactFieldOption / …_field_options   low, medium, high
    assigned value      ArtifactValue / artifact_values         ABC-29 → high
    the thing itself    Artifact / artifacts                    ABC-29, ABC-4
"""
import datetime as dt

from sqlalchemy import (
    Boolean, DateTime, ForeignKey, Integer, String, Text, UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from ..db import Base
from .base import TimestampMixin



class ArtifactType(TimestampMixin, Base):
    """Interface: "artifact", so a ticket, a hardware unit or a thing defined by oneself."""
    __tablename__ = "artifact_types"
    __table_args__ = (UniqueConstraint("key", name="uq_artifact_type_key"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    key: Mapped[str] = mapped_column(String(40), nullable=False, index=True)
    name: Mapped[str] = mapped_column(String(100), nullable=False)     # Einzahl
    plural: Mapped[str] = mapped_column(String(100), default="")
    icon: Mapped[str] = mapped_column(String(16), default="📦")
    color: Mapped[str] = mapped_column(String(20), default="#58a6ff")
    # issue | hardware_asset | generic: where the data lies.
    backing: Mapped[str] = mapped_column(String(20), default="generic")
    # Only for `generic`: limited to a project (NULL = everywhere).
    project_id: Mapped[int | None] = mapped_column(
        ForeignKey("projects.id", ondelete="CASCADE"), nullable=True, index=True)
    # Built-in types (ticket, hardware) must not be deleted: their states hang off hard wired
    # columns.
    builtin: Mapped[bool] = mapped_column(Boolean, default=False)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    description: Mapped[str] = mapped_column(Text, default="")



class ArtifactField(TimestampMixin, Base):
    """One field of an artifact, applying to all units of this artifact.

    `kind` uses the same language as the process forms (`FormField` in the frontend) so that
    two type worlds do not stand side by side. `multi` is the multiple switch: it decides
    whether a single ticket may carry one or several values.
    """
    __tablename__ = "artifact_fields"
    # The key is unique per artifact AND project: two projects may have a field of the same
    # name without getting in each other's way.
    __table_args__ = (
        UniqueConstraint("type_id", "project_id", "key", name="uq_artifact_field"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    type_id: Mapped[int] = mapped_column(
        ForeignKey("artifact_types.id", ondelete="CASCADE"), index=True)
    key: Mapped[str] = mapped_column(String(40), nullable=False)
    label: Mapped[str] = mapped_column(String(100), nullable=False)
    # text | number | date | boolean | select
    kind: Mapped[str] = mapped_column(String(20), default="text")
    multi: Mapped[bool] = mapped_column(Boolean, default=False)
    required: Mapped[bool] = mapped_column(Boolean, default=False)
    order: Mapped[int] = mapped_column(Integer, default=0)
    description: Mapped[str] = mapped_column(Text, default="")
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    # Origin of the values. Empty = free (the values stand in `artifact_values`); otherwise
    # the name of the real column of the detail table, for instance `agent_status` or
    # `serial_number`. That makes the state a field as well: there is no second state model.
    source: Mapped[str] = mapped_column(String(40), default="")
    # Where the selectable values come from when they do not stand in the own list:
    # issue_type | board_status | sprint | member | location (all project dependent).
    options_source: Mapped[str] = mapped_column(String(30), default="")
    # Built in: key, type and origin are locked and deleting is impossible. Exactly that
    # makes the naming convention for `status` reliable.
    builtin: Mapped[bool] = mapped_column(Boolean, default=False)
    # Addition of a single project (NULL = applies everywhere). That way a project owner
    # extends their tickets without changing those of everybody else.
    project_id: Mapped[int | None] = mapped_column(
        ForeignKey("projects.id", ondelete="CASCADE"), nullable=True, index=True)


class ArtifactFieldOption(Base):
    """One entry of the value list of a field (only meaningful with `kind='select'`)."""
    __tablename__ = "artifact_field_options"
    __table_args__ = (UniqueConstraint("field_id", "value", name="uq_artifact_field_option"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    field_id: Mapped[int] = mapped_column(
        ForeignKey("artifact_fields.id", ondelete="CASCADE"), index=True)
    # The stored value; the label may change without touching data.
    value: Mapped[str] = mapped_column(String(200), nullable=False)
    label: Mapped[str] = mapped_column(String(200), default="")
    color: Mapped[str] = mapped_column(String(20), default="")
    order: Mapped[int] = mapped_column(Integer, default=0)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    # Only meaningful with the state field, but rightly kept on the value:
    # todo | in_progress | done controls the board column and the evaluations …
    category: Mapped[str] = mapped_column(String(20), default="")
    # … and `waiting` highlights that a human is needed here.
    waiting: Mapped[bool] = mapped_column(Boolean, default=False)


class ArtifactValue(Base):
    """One value assigned to a concrete artifact. Several values = several rows.

    Hangs off `artifacts.id`, and because every ticket and every hardware unit has a row
    there, all artifacts carry their fields the same way.
    """
    __tablename__ = "artifact_values"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    artifact_id: Mapped[int] = mapped_column(
        ForeignKey("artifacts.id", ondelete="CASCADE"), index=True)
    field_id: Mapped[int] = mapped_column(
        ForeignKey("artifact_fields.id", ondelete="CASCADE"), index=True)
    # With choice fields the reference to the list, otherwise the free value as text.
    option_id: Mapped[int | None] = mapped_column(
        ForeignKey("artifact_field_options.id", ondelete="CASCADE"), nullable=True, index=True)
    value_text: Mapped[str] = mapped_column(Text, default="")
    order: Mapped[int] = mapped_column(Integer, default=0)


class Artifact(TimestampMixin, Base):
    """Instanz eines frei definierten Typs (`backing='generic'`).

    Ticket and hardware deliberately do NOT lie here: they keep their grown tables including
    board, sprints, lifecycle and procurement chain.
    """
    __tablename__ = "artifacts"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    type_id: Mapped[int] = mapped_column(
        ForeignKey("artifact_types.id", ondelete="CASCADE"), index=True)
    project_id: Mapped[int | None] = mapped_column(
        ForeignKey("projects.id", ondelete="CASCADE"), nullable=True, index=True)
    title: Mapped[str] = mapped_column(String(500), nullable=False)
    status_key: Mapped[str] = mapped_column(String(40), default="")
    # The free fields lie in `artifact_values`, one row per value, so that multiple values
    # and the value list stay referenceable.
    created_by: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    closed_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
