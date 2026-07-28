"""Artefakte: die gemeinsame Sicht auf alles, was einen Zustand hat.

Ticket und Hardware-Exemplar waren bisher zwei unverbundene Welten mit je eigener
Status-Achse (`TicketAgentStatus`, `PurchaseStatus`) — im Prozess-Editor führte das zu drei
verschiedenen Status-Aktionen, von denen je nach Ablauf zwei sinnlos waren.

Hier steht das Register: welche **Artefakt-Typen** es gibt und welche **Zustände** jeder
kennt — pflegbar in der Administration. Woher die Daten kommen, sagt `backing`:

    issue           → Tabelle `issues` (Zustand in `agent_status`)
    hardware_asset  → Tabelle `hardware_assets` (Zustand in `purchase_status`)
    generic         → Tabelle `artifacts` (frei definierte Typen)

Damit ist der Typ konfigurierbar, ohne dass Board, Sprints oder der KI-Lebenszyklus ihre
gewachsenen Tabellen verlieren. Neue, selbst definierte Typen landen in `artifacts`.

Darunter hängt das Feld-Modell nach Artefakt-Vorbild (`Artifacts → Fields → Values`): ein
Artefakt trägt beliebig viele **Felder**, ein Feld vom Typ „Auswahl" eine gepflegte
**Werteliste**, und am Feld steht, ob ein einzelnes Artefakt einen oder mehrere Werte daraus
tragen darf.

Ein Artefakt ist zunächst etwas Undefiniertes — seine Bedeutung bekommt es erst durch seine
Felder. Ticket und Hardware sind deshalb nichts Besonderes, sondern Artefakte mit einem
ausgelieferten Satz fester Felder. Ein Projekt darf beliebig eigene ergänzen; die
ausgelieferten lassen sich nicht entfernen, weil Board, Sprints und der KI-Lebenszyklus
darauf laufen.

Begriffe — die Oberfläche spricht anders als der Code, weil ein Umbenennen der Tabellen die
Fremdschlüssel aus `issues`, `hardware_assets` und `workflow_instances` mitzöge:

    Oberfläche          Code / Tabelle                          Beispiel
    Artefakt            ArtifactType / artifact_types           Ticket, Hardware
    Feld                ArtifactField / artifact_fields         Priorität
    Wert (Liste)        ArtifactFieldOption / …_field_options   niedrig, mittel, hoch
    zugeordneter Wert   ArtifactValue / artifact_values         ABC-29 → hoch
    die Sache selbst    Artifact / artifacts                    ABC-29, ABC-4
"""
import datetime as dt

from sqlalchemy import (
    Boolean, DateTime, ForeignKey, Integer, String, Text, UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from ..db import Base
from .base import TimestampMixin



class ArtifactType(TimestampMixin, Base):
    """Oberfläche: „Artefakt" — Ticket, Hardware-Exemplar oder ein selbst definiertes Ding."""
    __tablename__ = "artifact_types"
    __table_args__ = (UniqueConstraint("key", name="uq_artifact_type_key"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    key: Mapped[str] = mapped_column(String(40), nullable=False, index=True)
    name: Mapped[str] = mapped_column(String(100), nullable=False)     # Einzahl
    plural: Mapped[str] = mapped_column(String(100), default="")
    icon: Mapped[str] = mapped_column(String(16), default="📦")
    color: Mapped[str] = mapped_column(String(20), default="#58a6ff")
    # issue | hardware_asset | generic — wo die Daten liegen.
    backing: Mapped[str] = mapped_column(String(20), default="generic")
    # Nur für `generic`: auf ein Projekt begrenzt (NULL = überall).
    project_id: Mapped[int | None] = mapped_column(
        ForeignKey("projects.id", ondelete="CASCADE"), nullable=True, index=True)
    # Eingebaute Typen (Ticket, Hardware) dürfen nicht gelöscht werden — ihre Zustände
    # hängen an fest verdrahteten Spalten.
    builtin: Mapped[bool] = mapped_column(Boolean, default=False)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    description: Mapped[str] = mapped_column(Text, default="")



class ArtifactField(TimestampMixin, Base):
    """Ein Feld eines Artefakts — gilt für alle Exemplare dieses Artefakts.

    `kind` benutzt dieselbe Sprache wie die Prozess-Formulare (`FormField` im Frontend),
    damit nicht zwei Typ-Welten nebeneinander stehen. `multi` ist der Mehrfach-Schalter:
    er entscheidet, ob ein einzelnes Ticket einen oder mehrere Werte tragen darf.
    """
    __tablename__ = "artifact_fields"
    # Schlüssel eindeutig je Artefakt UND Projekt: zwei Projekte dürfen ein Feld
    # gleichen Namens haben, ohne sich in die Quere zu kommen.
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
    # Herkunft der Werte. Leer = frei (die Werte stehen in `artifact_values`); sonst der
    # Name der echten Spalte der Detailtabelle, z. B. `agent_status` oder `serial_number`.
    # Damit ist auch der Zustand nur noch ein Feld — es gibt kein zweites Zustands-Modell.
    source: Mapped[str] = mapped_column(String(40), default="")
    # Woher die Auswahlwerte kommen, wenn sie nicht in der eigenen Liste stehen:
    # issue_type | board_status | sprint | member | location (alle projektabhängig).
    options_source: Mapped[str] = mapped_column(String(30), default="")
    # Eingebaut: Schlüssel, Typ und Herkunft sind gesperrt, Löschen ist nicht möglich.
    # Genau das macht die Namenskonvention für `status` verlässlich.
    builtin: Mapped[bool] = mapped_column(Boolean, default=False)
    # Ergänzung eines einzelnen Projekts (NULL = gilt überall). So erweitert ein
    # Projekt-Eigentümer seine Tickets, ohne die aller anderen zu verändern.
    project_id: Mapped[int | None] = mapped_column(
        ForeignKey("projects.id", ondelete="CASCADE"), nullable=True, index=True)


class ArtifactFieldOption(Base):
    """Ein Eintrag der Werteliste eines Feldes (nur bei `kind='select'` von Bedeutung)."""
    __tablename__ = "artifact_field_options"
    __table_args__ = (UniqueConstraint("field_id", "value", name="uq_artifact_field_option"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    field_id: Mapped[int] = mapped_column(
        ForeignKey("artifact_fields.id", ondelete="CASCADE"), index=True)
    # Der gespeicherte Wert; die Beschriftung darf sich ändern, ohne Daten anzufassen.
    value: Mapped[str] = mapped_column(String(200), nullable=False)
    label: Mapped[str] = mapped_column(String(200), default="")
    color: Mapped[str] = mapped_column(String(20), default="")
    order: Mapped[int] = mapped_column(Integer, default=0)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    # Nur beim Zustands-Feld von Bedeutung, aber am Wert richtig aufgehoben:
    # todo | in_progress | done steuert Board-Spalte und Auswertungen …
    category: Mapped[str] = mapped_column(String(20), default="")
    # … und `waiting` hebt hervor, dass hier ein Mensch gebraucht wird.
    waiting: Mapped[bool] = mapped_column(Boolean, default=False)


class ArtifactValue(Base):
    """Ein am konkreten Artefakt zugeordneter Wert. Mehrere Werte = mehrere Zeilen.

    Hängt an `artifacts.id` — und weil jedes Ticket und jedes Hardware-Exemplar dort eine
    Zeile hat, tragen alle Artefakte ihre Felder auf demselben Weg.
    """
    __tablename__ = "artifact_values"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    artifact_id: Mapped[int] = mapped_column(
        ForeignKey("artifacts.id", ondelete="CASCADE"), index=True)
    field_id: Mapped[int] = mapped_column(
        ForeignKey("artifact_fields.id", ondelete="CASCADE"), index=True)
    # Bei Auswahl-Feldern der Verweis auf die Liste, sonst der freie Wert als Text.
    option_id: Mapped[int | None] = mapped_column(
        ForeignKey("artifact_field_options.id", ondelete="CASCADE"), nullable=True, index=True)
    value_text: Mapped[str] = mapped_column(Text, default="")
    order: Mapped[int] = mapped_column(Integer, default=0)


class Artifact(TimestampMixin, Base):
    """Instanz eines frei definierten Typs (`backing='generic'`).

    Ticket und Hardware liegen bewusst NICHT hier — sie behalten ihre gewachsenen Tabellen
    samt Board, Sprints, Lebenszyklus und Beschaffungskette.
    """
    __tablename__ = "artifacts"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    type_id: Mapped[int] = mapped_column(
        ForeignKey("artifact_types.id", ondelete="CASCADE"), index=True)
    project_id: Mapped[int | None] = mapped_column(
        ForeignKey("projects.id", ondelete="CASCADE"), nullable=True, index=True)
    title: Mapped[str] = mapped_column(String(500), nullable=False)
    status_key: Mapped[str] = mapped_column(String(40), default="")
    # Die freien Felder liegen in `artifact_values` — eine Zeile je Wert, damit
    # Mehrfachwerte und die Werteliste referenzierbar bleiben.
    created_by: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    closed_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
