"""Messreihen: Zahlen mit Zeitstempel, die ein Ablauf mitschreibt.

Ein Ablauf sah bisher immer nur den Augenblick. „Akku 25 %" ist für sich genommen
belanglos — erst die Reihe der letzten Wochen sagt, ob das Gerät in zwei Tagen oder in
zwei Monaten stehenbleibt. Genau diese Rückschlüsse waren nicht möglich: der Wert kam
mit dem Webhook an, wurde in eine Nachricht gegossen und war weg.

Bewusst allgemein gehalten und nicht auf Akkustände zugeschnitten: eine Reihe ist ein
Name, eine Einheit und eine Folge von (Zeitpunkt, Wert). Was daraus abgelesen wird —
Verbrauch pro Tag, Restlaufzeit, Vorwarnung — steht im Ablauf, nicht hier.
"""
from __future__ import annotations

import datetime as dt

from sqlalchemy import (
    DateTime, Float, ForeignKey, Integer, JSON, String, Text, UniqueConstraint, func,
)
from sqlalchemy.orm import Mapped, mapped_column

from ..db import Base
from .base import TimestampMixin


class MetricSeries(TimestampMixin, Base):
    """Eine benannte Reihe — „akku.shelter", „temperatur.serverraum"."""
    __tablename__ = "metric_series"
    __table_args__ = (UniqueConstraint("owner_user_id", "key", name="uq_metric_series_owner_key"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    # Reihen gehören einem Menschen — sie entstehen aus seinen Abläufen und enthalten
    # Betriebsdaten seiner Geräte.
    owner_user_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=True, index=True)
    key: Mapped[str] = mapped_column(String(120), nullable=False)
    name: Mapped[str] = mapped_column(String(200), default="")
    unit: Mapped[str] = mapped_column(String(20), default="")
    description: Mapped[str] = mapped_column(Text, default="")
    # Letzter Stand — spart der Übersicht den Blick in die Punkte.
    last_value: Mapped[float | None] = mapped_column(Float, nullable=True)
    last_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    # Wann zuletzt vorgewarnt wurde. Ohne diese Marke käme die Warnung „in 7 Tagen leer"
    # jeden Tag erneut — bei einem Gerät, das täglich meldet, wäre das die Sorte
    # Benachrichtigung, die man nach drei Tagen stummschaltet.
    warned_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    warned_value: Mapped[float | None] = mapped_column(Float, nullable=True)
    # Wann zuletzt gemeldet wurde, dass diese Reihe verstummt ist. Eigene Marke neben
    # `warned_at`, weil es eine andere Tatsache mit anderer Verfallsbedingung ist: die
    # Restlaufzeit-Warnung verfällt beim Auffüllen, die Stille beim nächsten Wert überhaupt.
    # Zusammengelegt würde eine Meldung die andere verschlucken.
    still_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class MetricPoint(Base):
    """Ein Messpunkt. `context` hält fest, woher er kam (Gerät, Lauf, Ereignis)."""
    __tablename__ = "metric_points"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    series_id: Mapped[int] = mapped_column(
        ForeignKey("metric_series.id", ondelete="CASCADE"), index=True)
    ts: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(),
                                            index=True)
    value: Mapped[float] = mapped_column(Float, nullable=False)
    context: Mapped[dict] = mapped_column(JSON, default=dict)
