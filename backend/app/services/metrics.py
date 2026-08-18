"""Was sich aus einer Messreihe ablesen lässt.

Der Kern ist eine Gerade durch die letzten Punkte: Wieviel ändert sich pro Tag, und wann
ist der Zielwert erreicht? Für Akkustände, Füllstände, Speicherplatz, Vorräte — überall
dort, wo eine Größe gleichmäßig in eine Richtung läuft und man den Aufschlag nicht
abwarten will.

Bewusst schlicht: kein Modell, das Wochenrhythmen lernt, sondern die Gerade, die ein
Mensch auch mit dem Lineal durch die Punkte legen würde. Sie ist erklärbar — man kann
im Zweifel selbst nachrechnen, warum die Warnung kam —, und wo die Annahme nicht trägt
(sprunghafte Werte), sagt das Bestimmtheitsmaß es dazu.
"""
from __future__ import annotations

import datetime as dt
import logging

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..models.metrics import MetricPoint, MetricSeries

log = logging.getLogger("traccoon.metrics")

FENSTER_TAGE = 30       # so weit zurück wird für den Trend gelesen
MIN_PUNKTE = 3          # darunter ist jede Gerade Zufall
# Und darunter ist sie Hochrechnung aus dem Rauschen: vier Spannungswerte aus drei Minuten
# ergaben „+14 V pro Tag". Zwischen erstem und letztem Punkt muss echte Zeit liegen.
MIN_SPANNE_TAGE = 0.5
# Wieviel ein Wert steigen muss, damit die Reihe als „aufgefüllt" gilt (neuer Akku,
# nachgefüllter Tank) und eine ausgesprochene Warnung wieder verfällt.
AUFFUELL_SPRUNG = 10.0


def _jetzt() -> dt.datetime:
    return dt.datetime.now(tz=dt.timezone.utc)


def _mit_zone(ts: dt.datetime) -> dt.datetime:
    """Zeitstempel ohne Zone als UTC lesen — SQLite gibt sie nackt zurück."""
    return ts if ts.tzinfo is not None else ts.replace(tzinfo=dt.timezone.utc)


async def reihe(db: AsyncSession, owner_id: int | None, key: str,
                *, anlegen: bool = False, name: str = "", einheit: str = "") -> MetricSeries | None:
    r = (await db.execute(select(MetricSeries).where(
        MetricSeries.owner_user_id == owner_id, MetricSeries.key == key))).scalar_one_or_none()
    if r is None and anlegen:
        r = MetricSeries(owner_user_id=owner_id, key=key, name=name or key, unit=einheit)
        db.add(r)
        await db.flush()
    return r


async def erfassen(db: AsyncSession, owner_id: int | None, key: str, wert: float, *,
                   name: str = "", einheit: str = "", ts: dt.datetime | None = None,
                   kontext: dict | None = None) -> tuple[MetricSeries, MetricPoint]:
    """Einen Wert festhalten. Die Reihe entsteht beim ersten Wert von selbst."""
    r = await reihe(db, owner_id, key, anlegen=True, name=name, einheit=einheit)
    if name and not r.name:
        r.name = name
    if einheit and not r.unit:
        r.unit = einheit
    punkt = MetricPoint(series_id=r.id, ts=ts or _jetzt(), value=float(wert),
                        context=kontext or {})
    db.add(punkt)
    # Ein deutlicher Anstieg heißt: nachgefüllt. Dann gilt die alte Warnung nicht mehr.
    if r.last_value is not None and float(wert) - r.last_value >= AUFFUELL_SPRUNG:
        r.warned_at = None
        r.warned_value = None
    r.last_value = float(wert)
    r.last_at = punkt.ts
    await db.flush()
    return r, punkt


async def punkte(db: AsyncSession, series_id: int, *, seit: dt.datetime | None = None,
                 grenze: int = 500) -> list[MetricPoint]:
    q = select(MetricPoint).where(MetricPoint.series_id == series_id)
    if seit is not None:
        q = q.where(MetricPoint.ts >= seit)
    return list((await db.execute(q.order_by(MetricPoint.ts.desc()).limit(grenze)))
                .scalars().all())[::-1]


def gerade(werte: list[tuple[float, float]]) -> tuple[float, float, float]:
    """Ausgleichsgerade y = a·x + b über (x=Tage, y=Wert) → (a, b, Bestimmtheitsmaß)."""
    n = len(werte)
    if n < 2:
        return 0.0, (werte[0][1] if werte else 0.0), 0.0
    sx = sum(x for x, _ in werte)
    sy = sum(y for _, y in werte)
    sxx = sum(x * x for x, _ in werte)
    sxy = sum(x * y for x, y in werte)
    nenner = n * sxx - sx * sx
    if abs(nenner) < 1e-9:
        return 0.0, sy / n, 0.0
    a = (n * sxy - sx * sy) / nenner
    b = (sy - a * sx) / n
    mittel = sy / n
    ss_tot = sum((y - mittel) ** 2 for _, y in werte)
    ss_res = sum((y - (a * x + b)) ** 2 for x, y in werte)
    r2 = 1.0 - (ss_res / ss_tot) if ss_tot > 1e-12 else 1.0
    return a, b, max(0.0, min(1.0, r2))


async def trend(db: AsyncSession, r: MetricSeries, *, ziel: float = 0.0,
                fenster_tage: int = FENSTER_TAGE) -> dict:
    """Wohin die Reihe läuft — und wann sie den Zielwert erreicht.

    `rest_tage` ist None, solange sich keine Richtung ablesen lässt: zu wenige Punkte,
    oder die Reihe bewegt sich vom Ziel weg. Kein Wert ist ehrlicher als eine Zahl, die
    aus zwei Messungen entsteht.
    """
    seit = _jetzt() - dt.timedelta(days=fenster_tage)
    ps = await punkte(db, r.id, seit=seit)
    ergebnis = {"punkte": len(ps), "wert": r.last_value, "einheit": r.unit,
                "pro_tag": None, "rest_tage": None, "leer_am": None, "guete": None,
                "erster_wert": ps[0].value if ps else None,
                "erster_am": _mit_zone(ps[0].ts).isoformat() if ps else None}
    if len(ps) < MIN_PUNKTE:
        return ergebnis
    basis = _mit_zone(ps[0].ts)
    werte = [((_mit_zone(p.ts) - basis).total_seconds() / 86400.0, p.value) for p in ps]
    ergebnis["spanne_tage"] = round(werte[-1][0], 2)
    if werte[-1][0] < MIN_SPANNE_TAGE:
        return ergebnis
    a, b, r2 = gerade(werte)
    ergebnis["pro_tag"] = round(a, 4)
    ergebnis["guete"] = round(r2, 3)
    jetzt_x = (_jetzt() - basis).total_seconds() / 86400.0
    aktuell = a * jetzt_x + b
    if abs(a) > 1e-9:
        rest = (ziel - aktuell) / a
        if rest >= 0:
            ergebnis["rest_tage"] = round(rest, 1)
            ergebnis["leer_am"] = (_jetzt() + dt.timedelta(days=rest)).date().isoformat()
    return ergebnis


def vorwarnen(r: MetricSeries, rest_tage: float | None, vorwarn_tage: float) -> bool:
    """Ob JETZT gewarnt werden soll — genau einmal je Auffüllung.

    Ohne diese Einmaligkeit käme bei einem Gerät, das täglich meldet, jeden Tag dieselbe
    Warnung; nach drei Tagen schaltet man sie stumm und verpasst genau die, auf die es
    ankam. Steigt der Wert wieder deutlich (`erfassen`), verfällt die Marke — ein neuer
    Akku darf erneut warnen.
    """
    if rest_tage is None or rest_tage > vorwarn_tage:
        return False
    if r.warned_at is not None:
        return False
    r.warned_at = _jetzt()
    r.warned_value = r.last_value
    return True
