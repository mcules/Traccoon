"""Vier Sprachen, eine Bedeutung: was ein Geraet meldet, wenn es seinen Standort meldet.

Jede App hat ihre eigene Fassung derselben Nachricht. Sie hier zu uebersetzen statt in vier
Endpunkten hat einen einfachen Grund: Der Unterschied liegt allein in den Namen der Felder,
nicht in dem, was sie bedeuten. Ein Endpunkt, der am Inhalt erkennt, womit er es zu tun hat,
kommt ohne Einstellung aus — man traegt die Adresse ein, und es laeuft.

Die vier:

* **OwnTracks** — `{"_type":"location","lat":…,"lon":…,"tst":…,"acc":…,"batt":…}`
* **Overland** — `{"locations":[GeoJSON-Feature,…]}`, ein Stapel; die Koordinaten stehen dort
  in der Reihenfolge **lon, lat** — die haeufigste Falle beim Umgang mit GeoJSON.
* **Traccar / OsmAnd** — kein Rumpf, alles in der Adresse: `?id=…&lat=…&lon=…&timestamp=…`
* **flach** — `{"lat":…,"lon":…}` oder `{"latitude":…,"longitude":…}`; das, was Home Assistant
  aus einer Vorlage schickt, und was jeder selbst zusammenbaut.
"""
from __future__ import annotations

import datetime as dt
from typing import Any

# Ein Zeitstempel, der weiter als das zurueckliegt, ist keine Sekundenangabe mehr, sondern
# eine in Millisekunden: 10^11 Sekunden waeren das Jahr 5138.
_MS_LIMIT = 100_000_000_000


def _number(value: Any) -> float | None:
    """Eine Zahl aus dem, was ankommt — oder nichts.

    Geraete schicken Zahlen als Text, mit Komma, mit Einheit dahinter, oder leer. Ein
    `float()` mit try/except waere zu grob: `"12,5 km/h"` soll 12,5 ergeben und nicht nichts.
    """
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    text = str(value).strip().replace(",", ".")
    if not text:
        return None
    allowed = "0123456789.-+eE"
    text = "".join(z for z in text if z in allowed).rstrip("eE+-")
    try:
        return float(text)
    except ValueError:
        return None


def moment(value: Any) -> dt.datetime | None:
    """Ein Zeitstempel aus Unix-Sekunden, Unix-Millisekunden oder ISO-Text."""
    if value is None:
        return None
    if isinstance(value, (int, float)) or (isinstance(value, str) and value.strip().isdigit()):
        number = float(value)
        if number <= 0:
            return None
        if number > _MS_LIMIT:
            number /= 1000.0
        try:
            return dt.datetime.fromtimestamp(number, tz=dt.timezone.utc)
        except (OverflowError, OSError, ValueError):
            return None
    text = str(value).strip()
    if not text:
        return None
    try:
        # `Z` kennt fromisoformat erst ab 3.11 sicher; das Ersetzen kostet nichts.
        read = dt.datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    return read if read.tzinfo else read.replace(tzinfo=dt.timezone.utc)


def _battery(value: Any) -> float | None:
    """Akkustand in Prozent.

    OwnTracks meldet 0–100, andere melden 0–1 als Bruchteil. Ein Wert unter 1 ist deshalb
    zweideutig — hier gilt er als Bruchteil, weil ein Telefon mit 0,8 % Akku laengst aus
    waere. Genau diese Verwechslung hat beim Weg nach dawarich schon einmal 8200 % ergeben.
    """
    number = _number(value)
    if number is None:
        return None
    if 0 < number <= 1:
        number *= 100
    return max(0.0, min(100.0, number))


def _fix(lat: Any, lon: Any, ts: Any, *, accuracy=None, altitude=None, speed=None,
         course=None, battery=None, source: str = "", raw: Any = None) -> dict | None:
    """Ein Punkt in Traccoons Form — oder nichts, wenn keine Koordinate drinsteckt."""
    la, lo = _number(lat), _number(lon)
    if la is None or lo is None:
        return None
    extra = {"accuracy": _number(accuracy), "altitude": _number(altitude),
              "speed": _number(speed), "course": _number(course), "battery": _battery(battery)}
    return {
        "lat": la, "lon": lo, "ts": moment(ts),
        # Leere Felder fliegen raus: Sonst stuende in jedem Punkt fuenfmal `null`, und bei
        # einer Million Punkten ist das ein spuerbarer Teil der Tabelle.
        "extra": {n: w for n, w in extra.items() if w is not None},
        "source": source,
        "raw": raw,
    }


def normalise(payload: Any, query: dict | None = None) -> list[dict]:
    """Alles, was ankommt, als Liste von Punkten. Unverstaendliches ergibt eine leere Liste."""
    query = {k.lower(): v for k, v in (query or {}).items()}
    data = payload if isinstance(payload, dict) else {}

    # Overland: ein Stapel GeoJSON-Features.
    if isinstance(data.get("locations"), list):
        return _overland(data["locations"])

    # OwnTracks: meldet auch Wegpunkte und Statusnachrichten — nur Standorte interessieren.
    if data.get("_type"):
        if data["_type"] not in ("location", "transition"):
            return []
        p = _fix(data.get("lat"), data.get("lon"), data.get("tst"),
                 accuracy=data.get("acc"), altitude=data.get("alt"),
                 speed=data.get("vel"), course=data.get("cog"),
                 battery=data.get("batt"), source="owntracks", raw=data)
        return [p] if p else []

    # Traccar/OsmAnd: alles in der Adresse. Erkennbar daran, dass der Rumpf nichts hergibt,
    # die Adresse aber Koordinaten traegt.
    if not data and ("lat" in query or "latitude" in query):
        p = _fix(query.get("lat") or query.get("latitude"),
                 query.get("lon") or query.get("longitude"),
                 query.get("timestamp") or query.get("ts"),
                 accuracy=query.get("accuracy") or query.get("hdop"),
                 altitude=query.get("altitude") or query.get("altitude_m"),
                 speed=query.get("speed"), course=query.get("bearing") or query.get("heading"),
                 battery=query.get("batt") or query.get("battery"),
                 source="traccar", raw=dict(query))
        return [p] if p else []

    # Flach — Home Assistant und alles Selbstgebaute.
    p = _fix(data.get("lat", data.get("latitude")),
             data.get("lon", data.get("lng", data.get("longitude"))),
             data.get("ts", data.get("timestamp", data.get("time"))),
             accuracy=data.get("accuracy", data.get("gps_accuracy", data.get("acc"))),
             altitude=data.get("altitude", data.get("alt")),
             speed=data.get("speed"),
             course=data.get("course", data.get("bearing", data.get("heading"))),
             battery=data.get("battery", data.get("batt", data.get("battery_level"))),
             source=str(data.get("source") or "api"), raw=data)
    return [p] if p else []


def _overland(entries: list) -> list[dict]:
    points = []
    for e in entries:
        if not isinstance(e, dict):
            continue
        coord = ((e.get("geometry") or {}).get("coordinates") or [])
        eig = e.get("properties") or {}
        if len(coord) < 2:
            continue
        # GeoJSON zaehlt lon zuerst. Wer das vertauscht, landet mitten im Indischen Ozean.
        p = _fix(coord[1], coord[0], eig.get("timestamp"),
                 accuracy=eig.get("horizontal_accuracy"), altitude=eig.get("altitude"),
                 speed=eig.get("speed"), course=eig.get("course"),
                 battery=eig.get("battery_level"), source="overland", raw=eig)
        if p:
            points.append(p)
    return points
