"""Den Standortbestand aus dawarich uebernehmen.

dawarich war die Zwischenstation, solange Traccoon selbst keine Standorte kannte. Was dort
liegt, sind zwei sehr verschiedene Dinge: die Spur des Handys der letzten Tage und ein von
Hand hochgeladener Google-Verlauf, der bis 2012 zurueckreicht. Beides soll erhalten bleiben,
in getrennten Reihen — der Google-Bestand ist ein Archiv, kein Geraet, das noch meldet.

Der Ruhefilter bleibt hier bewusst aus: Was schon einmal gespeichert wurde, ist die
Entscheidung von damals. Ihn nachtraeglich anzuwenden hiesse, fremde Daten auszuduennen, und
dabei faellt in einem Google-Verlauf mit stundenlangen Luecken das Falsche weg.

Aufruf (im Backend-Container):

    python scripts/import_dawarich.py <csv> <besitzer-id> [--trocken]

Die CSV kommt aus dawarich:

    \\copy (select coalesce(tracker_id,'google-verlauf'), timestamp,
                  st_y(lonlat::geometry), st_x(lonlat::geometry),
                  accuracy, altitude, battery, velocity
           from points order by timestamp) to stdout with (format csv)
"""
from __future__ import annotations

import asyncio
import csv
import datetime as dt
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy import select                                   # noqa: E402
from app.db import SessionLocal                                 # noqa: E402
from app.models.series import Series, SeriesPoint               # noqa: E402

# Welcher `tracker_id` in welche Reihe wandert, und wie sie heisst.
REIHEN = {
    "s26-ultra": ("handy.s26-ultra", "S26 Ultra", "#3b82f6"),
    "google-verlauf": ("archiv.google-verlauf", "Google-Verlauf (Archiv)", "#6b7280"),
}
STAPEL = 500


def _kennung(ts, lat, lon) -> tuple:
    """Woran ein Punkt wiedererkannt wird: Sekunde und Ort.

    Auf sechs Nachkommastellen gerundet — das sind gut 10 cm, feiner als jedes GPS, und
    gleichzeitig unempfindlich gegen die letzte Stelle, die beim Weg durch zwei Datenbanken
    schon einmal kippt.
    """
    genau = ts.replace(tzinfo=None) if ts else None
    return (genau, round(lat, 6) if lat is not None else None,
            round(lon, 6) if lon is not None else None)


def _zahl(text: str) -> float | None:
    text = (text or "").strip()
    if not text:
        return None
    try:
        return float(text)
    except ValueError:
        return None


async def hole_reihe(db, besitzer: int, key: str, name: str, farbe: str) -> Series:
    reihe = (await db.execute(select(Series).where(
        Series.owner_user_id == besitzer, Series.key == key))).scalar_one_or_none()
    if reihe is None:
        reihe = Series(owner_user_id=besitzer, key=key, kind="location", name=name,
                       color=farbe, settings={})
        db.add(reihe)
        await db.flush()
        print(f"  Reihe {key} angelegt")
    return reihe


async def lauf(csv_pfad: str, besitzer: int, trocken: bool) -> None:
    with open(csv_pfad, newline="") as f:
        zeilen = list(csv.reader(f))
    print(f"{len(zeilen)} Zeilen gelesen")

    async with SessionLocal() as db:
        reihen: dict[str, Series] = {}
        # Was schon drin liegt, damit ein zweiter Lauf nichts verdoppelt. Als Kennung dient
        # Zeitstempel **und** Position: Der Zeitstempel allein waere naheliegend, verschluckt
        # aber echte Punkte — im Google-Bestand stehen vier Paare, die dieselbe Sekunde
        # tragen und trotzdem an verschiedenen Orten liegen.
        bekannt: dict[str, set] = {}
        gezaehlt = {"neu": 0, "doppelt": 0, "kaputt": 0}

        for i, zeile in enumerate(zeilen):
            if len(zeile) < 4:
                gezaehlt["kaputt"] += 1
                continue
            geraet, ts_roh, lat_roh, lon_roh = zeile[0], zeile[1], zeile[2], zeile[3]
            if geraet not in REIHEN:
                gezaehlt["kaputt"] += 1
                continue

            key, name, farbe = REIHEN[geraet]
            if key not in reihen:
                reihen[key] = await hole_reihe(db, besitzer, key, name, farbe)
                vorhanden = (await db.execute(select(
                    SeriesPoint.ts, SeriesPoint.lat, SeriesPoint.lon).where(
                    SeriesPoint.series_id == reihen[key].id))).all()
                bekannt[key] = {_kennung(t, la, lo) for t, la, lo in vorhanden if t}

            lat, lon, ts_zahl = _zahl(lat_roh), _zahl(lon_roh), _zahl(ts_roh)
            if lat is None or lon is None or ts_zahl is None:
                gezaehlt["kaputt"] += 1
                continue
            ts = dt.datetime.fromtimestamp(ts_zahl, tz=dt.timezone.utc)
            kennung = _kennung(ts, lat, lon)
            if kennung in bekannt[key]:
                gezaehlt["doppelt"] += 1
                continue
            bekannt[key].add(kennung)

            extra = {}
            for feld, spalte in (("accuracy", 4), ("altitude", 5), ("battery", 6),
                                 ("speed", 7)):
                wert = _zahl(zeile[spalte]) if len(zeile) > spalte else None
                if wert is not None:
                    extra[feld] = wert

            if not trocken:
                db.add(SeriesPoint(series_id=reihen[key].id, ts=ts, lat=lat, lon=lon,
                                   extra=extra, source="import", context={"aus": "dawarich"}))
            gezaehlt["neu"] += 1
            if not trocken and gezaehlt["neu"] % STAPEL == 0:
                await db.flush()
                print(f"  {gezaehlt['neu']} …")

        if trocken:
            print(f"TROCKEN: {gezaehlt}")
            return

        # Zaehler und letzten Stand nachziehen — sonst zeigt die Uebersicht null Punkte.
        for key, reihe in reihen.items():
            punkte = (await db.execute(select(SeriesPoint).where(
                SeriesPoint.series_id == reihe.id)
                .order_by(SeriesPoint.ts.desc()).limit(1))).scalars().all()
            reihe.points = len((await db.execute(select(SeriesPoint.id).where(
                SeriesPoint.series_id == reihe.id))).scalars().all())
            if punkte:
                letzter = punkte[0]
                ts = letzter.ts if letzter.ts.tzinfo else letzter.ts.replace(
                    tzinfo=dt.timezone.utc)
                if reihe.last_at is None or ts >= (
                        reihe.last_at if reihe.last_at.tzinfo
                        else reihe.last_at.replace(tzinfo=dt.timezone.utc)):
                    reihe.state = {**(reihe.state or {}), "lat": letzter.lat,
                                   "lon": letzter.lon, **(letzter.extra or {})}
                    reihe.last_at = ts
            print(f"  {key}: {reihe.points} Punkte, zuletzt {reihe.last_at}")

        await db.commit()
        print(f"fertig: {gezaehlt}")


if __name__ == "__main__":
    if len(sys.argv) < 3:
        print(__doc__)
        raise SystemExit(2)
    asyncio.run(lauf(sys.argv[1], int(sys.argv[2]), "--trocken" in sys.argv))
