"""Ablagen: Texte, die ein Ablauf schreibt und die man später ansehen will.

Ein Ablauf konnte seinen Text nirgends hinlegen. Der Rückblick, den ein Agent jeden Morgen
schreibt, landete im Ausgabefeld eines Job-Laufs — abgeschnitten bei 20.000 Zeichen, ohne
Überschrift, ohne Ansicht, und nur zu finden, wenn man wusste, welcher Lauf es war. Die
Meldung dazu verwies auf eine Seite, die es nie gab.

Aufgebaut wie die Messreihen und aus demselben Grund: Eine Ablage ist ein Name und eine
Folge von Fassungen. Was darin steht (Rückblick, Bericht, Protokoll, Notiz) und was daraus
folgt, weiß der Ablauf — hier steht nur, dass es aufgehoben wird.
"""
from __future__ import annotations

import datetime as dt

from sqlalchemy import (
    DateTime, ForeignKey, Integer, JSON, String, Text, UniqueConstraint, func,
)
from sqlalchemy.orm import Mapped, mapped_column

from ..db import Base
from .base import TimestampMixin


class DocSeries(TimestampMixin, Base):
    """Eine benannte Ablage: ki-tech-news, wochenbericht, serverraum-protokoll."""
    __tablename__ = "doc_series"
    __table_args__ = (UniqueConstraint("owner_user_id", "key", name="uq_doc_series_owner_key"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    # Ablagen gehören einem Menschen: Sie entstehen aus seinen Abläufen und enthalten, was
    # deren Agenten für ihn geschrieben haben.
    owner_user_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=True, index=True)
    key: Mapped[str] = mapped_column(String(120), nullable=False)
    name: Mapped[str] = mapped_column(String(200), default="")
    description: Mapped[str] = mapped_column(Text, default="")
    # Wie viele Fassungen aufgehoben werden. Ein täglicher Rückblick wäre nach einem Jahr
    # 365 Fassungen, von denen niemand mehr als die letzten paar Dutzend ansieht.
    keep: Mapped[int] = mapped_column(Integer, default=60)
    # Letzter Stand; erspart der Übersicht den Blick in die Fassungen.
    last_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_title: Mapped[str] = mapped_column(String(300), default="")


class DocEntry(Base):
    """Eine Fassung. `context` hält fest, woher sie kam (Ablauf, Lauf, Job)."""
    __tablename__ = "doc_entries"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    series_id: Mapped[int] = mapped_column(
        ForeignKey("doc_series.id", ondelete="CASCADE"), index=True)
    ts: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(),
                                            index=True)
    title: Mapped[str] = mapped_column(String(300), default="")
    body: Mapped[str] = mapped_column(Text, default="")
    # markdown | text. Kein HTML: Was hier hineingeht, kommt von einem Modell, und HTML aus
    # einem Modell wäre eine fremde Seite in der eigenen Oberfläche.
    format: Mapped[str] = mapped_column(String(20), default="markdown")
    context: Mapped[dict] = mapped_column(JSON, default=dict)
