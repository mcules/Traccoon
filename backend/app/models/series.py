"""Data series: named sequences of points that have a kind.

Traccoon knew two sorts of them so far, each with tables of its own: metric series (a number
with a unit) and stores (a heading and a text). With locations a third would have come along,
and with it the same structure a third time — a head with an owner, points with a timestamp,
Freigaben, Aufraeumen.

So once, with a `kind` column: `number`, `location`, `text`. What makes a kind special sits
in `settings` and in the columns only it fills. What all of them share — who owns the series,
who may see it, when something last arrived — stands here exactly once.

Deliberately **not** called `Location`: the name is taken in the house, there it is the
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

# The kinds there are. A new one costs a line here plus the columns it fills.
KINDS = ("number", "location", "text")


class Series(TimestampMixin, Base):
    """Eine benannte Reihe: akku.shelter, handy.s26-ultra, post.eingang."""
    __tablename__ = "series"
    __table_args__ = (UniqueConstraint("owner_user_id", "key", name="uq_series_owner_key"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    # Series belong to a person: they come out of their flows and hold data from their
    # devices. NULL means system-wide — only an admin may create that.
    owner_user_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=True, index=True)
    key: Mapped[str] = mapped_column(String(120), nullable=False)
    kind: Mapped[str] = mapped_column(String(20), default="number", index=True)
    name: Mapped[str] = mapped_column(String(200), default="")
    description: Mapped[str] = mapped_column(Text, default="")
    # The colour a view draws this series in (#rrggbb). Belongs on the series and not in a
    # plugin: whoever sees two phones on a map should recognise them in the chart
    # in denselben Farben wiederfinden.
    color: Mapped[str] = mapped_column(String(7), default="")


    # Art-abhaengig: unit (number) · min_distance_m, min_interval_s, max_accuracy_m
    # (location) · keep_entries (text). Deliberately one JSON instead of twelve columns of
    # Art acht leer waeren.
    settings: Mapped[dict] = mapped_column(JSON, default=dict)
    # The latest state, denormalised: saves the overview and the map a look into the
    # Punkte. Art-abhaengig belegt (value/lat/lon/battery/places).
    state: Mapped[dict] = mapped_column(JSON, default=dict)
    last_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    # How many points lie in it. Counted while writing instead of on every look: `count(*)`
    # over a million rows for one list row would be asking too much.
    points: Mapped[int] = mapped_column(Integer, default=0)

    # Trigger marks, adopted from MetricSeries: warn once, report silence once.
    warned_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    warned_value: Mapped[float | None] = mapped_column(Float, nullable=True)
    still_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    # Only on series that are supplied from outside. The hash looks up in constant time, the
    # encrypted version lets the address be shown again — one has to type it into a phone, and
    # "see it once and then never again" is no help there.
    token_hash: Mapped[str] = mapped_column(String(64), default="", index=True)
    token_enc: Mapped[str] = mapped_column(Text, default="")

    active: Mapped[bool] = mapped_column(Boolean, default=True)


class SeriesPoint(Base):
    """A point. Which columns are filled is decided by the kind of the series.

    One table with empty columns instead of three tables: in Postgres a NULL costs almost
    nothing thanks to the null bitmap, `lat`/`lon` stay indexable, and a new kind is then a
    column — not a new table with its own pruning and its own grants.
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

    # kind=location. Float and not Numeric: the arithmetic happens in floating point anyway,
    # and PostGIS is not available — the extension is not part of the image.
    lat: Mapped[float | None] = mapped_column(Float, nullable=True)
    lon: Mapped[float | None] = mapped_column(Float, nullable=True)
    # accuracy, altitude, speed, course, battery — everything a device sends along that does
    # not deserve a column of its own because hardly anyone searches by it.
    extra: Mapped[dict] = mapped_column(JSON, default=dict)

    # kind=text
    title: Mapped[str] = mapped_column(String(200), default="")
    body: Mapped[str] = mapped_column(Text, default="")
    format: Mapped[str] = mapped_column(String(20), default="")


class SeriesPlace(Base):
    """A named place with a radius — the geofence.

    Entering and leaving are events like an incoming mail: they start flows. That is exactly
    why the places live in Traccoon and not in a map next to it.
    """
    __tablename__ = "series_places"
    __table_args__ = (UniqueConstraint("owner_user_id", "key", name="uq_series_place_owner_key"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    owner_user_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=True, index=True)
    # Set: applies to this one series only. NULL: to all location series of the person —
    # the normal case, because "home" is the same place for every device.
    series_id: Mapped[int | None] = mapped_column(
        ForeignKey("series.id", ondelete="CASCADE"), nullable=True, index=True)
    key: Mapped[str] = mapped_column(String(120), nullable=False)
    name: Mapped[str] = mapped_column(String(200), default="")
    lat: Mapped[float] = mapped_column(Float, nullable=False)
    lon: Mapped[float] = mapped_column(Float, nullable=False)
    radius_m: Mapped[int] = mapped_column(Integer, default=150)
    color: Mapped[str] = mapped_column(String(7), default="")
    # Off: the place is only drawn and fires nothing.
    notify: Mapped[bool] = mapped_column(Boolean, default=True)


class SeriesShare(Base):
    """Who may see somebody else's series.

    A table of its own instead of `ResourceGrant`: its `project_id` is NOT NULL and granting
    hangs under `/projects/{id}/resource-grants` behind the maintainer role. Series are
    project-less like metric series and stores. Three columns are cheaper than
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
