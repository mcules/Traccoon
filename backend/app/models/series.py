"""Datenreihen: benannte Folgen von Punkten, die eine Art haben.

Traccoon kannte bisher zwei Sorten davon, jede mit eigenen Tabellen: Messreihen (Zahl mit
Einheit) und Ablagen (Titel und Text). Mit den Standorten waere eine dritte dazugekommen,
und mit ihr ein drittes Mal derselbe Aufbau — Kopf mit Besitzer, Punkte mit Zeitstempel,
Freigaben, Aufraeumen.

Also einmal, mit einer `kind`-Spalte: `number`, `location`, `text`. Was eine Art besonders
macht, steht in `settings` und in den Spalten, die nur sie fuellt. Was alle teilen — wem die
Reihe gehoert, wer sie sehen darf, wann zuletzt etwas ankam — steht genau einmal hier.

Absichtlich **nicht** `Location` genannt: Der Name ist im Haus vergeben, dort ist er der
Standortbaum der Hardware-Verwaltung.
"""
from __future__ import annotations

import datetime as dt

from sqlalchemy import (
    Boolean, DateTime, Float, ForeignKey, Integer, JSON, String, Text, UniqueConstraint, func,
)
from sqlalchemy.orm import Mapped, mapped_column

from ..db import Base
from .base import TimestampMixin

# Die Arten, die es gibt. Eine neue kostet eine Zeile hier plus die Spalten, die sie fuellt.
ARTEN = ("number", "location", "text")


class Series(TimestampMixin, Base):
    """Eine benannte Reihe: akku.shelter, handy.s26-ultra, post.eingang."""
    __tablename__ = "series"
    __table_args__ = (UniqueConstraint("owner_user_id", "key", name="uq_series_owner_key"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    # Reihen gehoeren einem Menschen: Sie entstehen aus seinen Ablaeufen und enthalten Daten
    # seiner Geraete. NULL heisst systemweit — das darf nur ein Admin anlegen.
    owner_user_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=True, index=True)
    key: Mapped[str] = mapped_column(String(120), nullable=False)
    kind: Mapped[str] = mapped_column(String(20), default="number", index=True)
    name: Mapped[str] = mapped_column(String(200), default="")
    description: Mapped[str] = mapped_column(Text, default="")
    # Farbe, in der eine Darstellung diese Reihe zeichnet (#rrggbb). Gehoert an die Reihe und
    # nicht in ein Plugin: Wer zwei Handys auf einer Karte sieht, soll sie auch im Diagramm
    # in denselben Farben wiederfinden.
    color: Mapped[str] = mapped_column(String(7), default="")

    # Wohin die Punkte geschrieben werden. NULL heisst: in diese Datenbank.
    store_id: Mapped[int | None] = mapped_column(
        ForeignKey("data_stores.id", ondelete="SET NULL"), nullable=True, index=True)
    # Womit der Mensch rechnet — die Grundlage fuer den Speichervorschlag.
    expected_rows: Mapped[int] = mapped_column(Integer, default=0)

    # Art-abhaengig: unit (number) · min_distance_m, min_interval_s, max_accuracy_m
    # (location) · keep_entries (text). Bewusst ein JSON statt zwoelf Spalten, von denen je
    # Art acht leer waeren.
    settings: Mapped[dict] = mapped_column(JSON, default=dict)
    # Der letzte Stand, denormalisiert: erspart der Uebersicht und der Karte den Blick in die
    # Punkte. Art-abhaengig belegt (value/lat/lon/battery/places).
    state: Mapped[dict] = mapped_column(JSON, default=dict)
    last_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    # Wieviele Punkte drin liegen. Gezaehlt beim Schreiben statt bei jedem Blick: `count(*)`
    # ueber eine Million Zeilen fuer eine Listenzeile waere zuviel verlangt.
    points: Mapped[int] = mapped_column(Integer, default=0)

    # Marken der Auslöser, aus MetricSeries uebernommen: einmal warnen, einmal Stille melden.
    warned_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    warned_value: Mapped[float | None] = mapped_column(Float, nullable=True)
    still_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    # Nur bei Reihen, die von aussen beliefert werden. Der Hash sucht in gleichbleibender
    # Zeit, die verschluesselte Fassung laesst die Adresse noch einmal anzeigen — man muss sie
    # in ein Telefon eintragen, und "einmal sehen und dann nie wieder" ist dort keine Hilfe.
    token_hash: Mapped[str] = mapped_column(String(64), default="", index=True)
    token_enc: Mapped[str] = mapped_column(Text, default="")

    active: Mapped[bool] = mapped_column(Boolean, default=True)


class SeriesPoint(Base):
    """Ein Punkt. Welche Spalten belegt sind, entscheidet die Art der Reihe.

    Eine Tabelle mit leeren Spalten statt drei Tabellen: In Postgres kostet ein NULL ueber die
    Null-Bitmap fast nichts, `lat`/`lon` bleiben indizierbar, und eine neue Art ist dann eine
    Spalte — nicht eine neue Tabelle mit eigenem Aufraeumen und eigenen Freigaben.
    """
    __tablename__ = "series_points"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    series_id: Mapped[int] = mapped_column(
        ForeignKey("series.id", ondelete="CASCADE"), index=True)
    ts: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(),
                                            index=True)
    # Woher der Punkt kam: ha | traccar | owntracks | overland | flow | import | api
    source: Mapped[str] = mapped_column(String(30), default="")
    context: Mapped[dict] = mapped_column(JSON, default=dict)

    # kind=number
    value: Mapped[float | None] = mapped_column(Float, nullable=True)

    # kind=location. Float und nicht Numeric: Gerechnet wird ohnehin in Fliesskomma, und
    # PostGIS steht nicht zur Verfuegung — die Erweiterung ist im Abbild nicht enthalten.
    lat: Mapped[float | None] = mapped_column(Float, nullable=True)
    lon: Mapped[float | None] = mapped_column(Float, nullable=True)
    # accuracy, altitude, speed, course, battery — alles, was ein Geraet mitschickt und was
    # keine eigene Spalte verdient, weil kaum jemand danach sucht.
    extra: Mapped[dict] = mapped_column(JSON, default=dict)

    # kind=text
    title: Mapped[str] = mapped_column(String(200), default="")
    body: Mapped[str] = mapped_column(Text, default="")
    format: Mapped[str] = mapped_column(String(20), default="")


class SeriesPlace(Base):
    """Ein benannter Ort mit Radius — der Geozaun.

    Betreten und Verlassen sind Ereignisse wie eine eingegangene Mail: Sie starten Ablaeufe.
    Genau deshalb liegen die Standorte in Traccoon und nicht in einer Karte daneben.
    """
    __tablename__ = "series_places"
    __table_args__ = (UniqueConstraint("owner_user_id", "key", name="uq_series_place_owner_key"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    owner_user_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=True, index=True)
    # Gesetzt: gilt nur fuer diese eine Reihe. NULL: fuer alle Standortreihen des Menschen —
    # der Normalfall, denn "zu Hause" ist fuer jedes Geraet derselbe Ort.
    series_id: Mapped[int | None] = mapped_column(
        ForeignKey("series.id", ondelete="CASCADE"), nullable=True, index=True)
    key: Mapped[str] = mapped_column(String(120), nullable=False)
    name: Mapped[str] = mapped_column(String(200), default="")
    lat: Mapped[float] = mapped_column(Float, nullable=False)
    lon: Mapped[float] = mapped_column(Float, nullable=False)
    radius_m: Mapped[int] = mapped_column(Integer, default=150)
    color: Mapped[str] = mapped_column(String(7), default="")
    # Aus: der Ort wird nur gezeichnet und loest nichts aus.
    notify: Mapped[bool] = mapped_column(Boolean, default=True)


class SeriesShare(Base):
    """Wer eine fremde Reihe sehen darf.

    Eine eigene Tabelle statt `ResourceGrant`: Dessen `project_id` ist NOT NULL und die
    Vergabe haengt unter `/projects/{id}/resource-grants` hinter der Maintainer-Rolle. Reihen
    sind wie Messreihen und Ablagen projektlos. Drei Spalten sind billiger, als das
    Projektmodell dafuer aufzuweichen.
    """
    __tablename__ = "series_shares"
    __table_args__ = (UniqueConstraint("series_id", "user_id", name="uq_series_share"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    series_id: Mapped[int] = mapped_column(
        ForeignKey("series.id", ondelete="CASCADE"), index=True)
    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True)
    level: Mapped[str] = mapped_column(String(10), default="view")   # view | manage
    created_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True),
                                                    server_default=func.now())
