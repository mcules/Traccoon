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
_MS_GRENZE = 100_000_000_000


def _zahl(wert: Any) -> float | None:
    """Eine Zahl aus dem, was ankommt — oder nichts.

    Geraete schicken Zahlen als Text, mit Komma, mit Einheit dahinter, oder leer. Ein
    `float()` mit try/except waere zu grob: `"12,5 km/h"` soll 12,5 ergeben und nicht nichts.
    """
    if wert is None or isinstance(wert, bool):
        return None
    if isinstance(wert, (int, float)):
        return float(wert)
    text = str(wert).strip().replace(",", ".")
    if not text:
        return None
    erlaubt = "0123456789.-+eE"
    text = "".join(z for z in text if z in erlaubt).rstrip("eE+-")
    try:
        return float(text)
    except ValueError:
        return None


def zeitpunkt(wert: Any) -> dt.datetime | None:
    """Ein Zeitstempel aus Unix-Sekunden, Unix-Millisekunden oder ISO-Text."""
    if wert is None:
        return None
    if isinstance(wert, (int, float)) or (isinstance(wert, str) and wert.strip().isdigit()):
        zahl = float(wert)
        if zahl <= 0:
            return None
        if zahl > _MS_GRENZE:
            zahl /= 1000.0
        try:
            return dt.datetime.fromtimestamp(zahl, tz=dt.timezone.utc)
        except (OverflowError, OSError, ValueError):
            return None
    text = str(wert).strip()
    if not text:
        return None
    try:
        # `Z` kennt fromisoformat erst ab 3.11 sicher; das Ersetzen kostet nichts.
        gelesen = dt.datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    return gelesen if gelesen.tzinfo else gelesen.replace(tzinfo=dt.timezone.utc)


def _akku(wert: Any) -> float | None:
    """Akkustand in Prozent.

    OwnTracks meldet 0–100, andere melden 0–1 als Bruchteil. Ein Wert unter 1 ist deshalb
    zweideutig — hier gilt er als Bruchteil, weil ein Telefon mit 0,8 % Akku laengst aus
    waere. Genau diese Verwechslung hat beim Weg nach dawarich schon einmal 8200 % ergeben.
    """
    zahl = _zahl(wert)
    if zahl is None:
        return None
    if 0 < zahl <= 1:
        zahl *= 100
    return max(0.0, min(100.0, zahl))


def _fix(lat: Any, lon: Any, ts: Any, *, accuracy=None, altitude=None, speed=None,
         course=None, battery=None, quelle: str = "", roh: Any = None) -> dict | None:
    """Ein Punkt in Traccoons Form — oder nichts, wenn keine Koordinate drinsteckt."""
    la, lo = _zahl(lat), _zahl(lon)
    if la is None or lo is None:
        return None
    zusatz = {"accuracy": _zahl(accuracy), "altitude": _zahl(altitude),
              "speed": _zahl(speed), "course": _zahl(course), "battery": _akku(battery)}
    return {
        "lat": la, "lon": lo, "ts": zeitpunkt(ts),
        # Leere Felder fliegen raus: Sonst stuende in jedem Punkt fuenfmal `null`, und bei
        # einer Million Punkten ist das ein spuerbarer Teil der Tabelle.
        "extra": {n: w for n, w in zusatz.items() if w is not None},
        "source": quelle,
        "raw": roh,
    }


def normalisiere(nutzlast: Any, query: dict | None = None) -> list[dict]:
    """Alles, was ankommt, als Liste von Punkten. Unverstaendliches ergibt eine leere Liste."""
    query = {k.lower(): v for k, v in (query or {}).items()}
    daten = nutzlast if isinstance(nutzlast, dict) else {}

    # Overland: ein Stapel GeoJSON-Features.
    if isinstance(daten.get("locations"), list):
        return _overland(daten["locations"])

    # OwnTracks: meldet auch Wegpunkte und Statusnachrichten — nur Standorte interessieren.
    if daten.get("_type"):
        if daten["_type"] not in ("location", "transition"):
            return []
        p = _fix(daten.get("lat"), daten.get("lon"), daten.get("tst"),
                 accuracy=daten.get("acc"), altitude=daten.get("alt"),
                 speed=daten.get("vel"), course=daten.get("cog"),
                 battery=daten.get("batt"), quelle="owntracks", roh=daten)
        return [p] if p else []

    # Traccar/OsmAnd: alles in der Adresse. Erkennbar daran, dass der Rumpf nichts hergibt,
    # die Adresse aber Koordinaten traegt.
    if not daten and ("lat" in query or "latitude" in query):
        p = _fix(query.get("lat") or query.get("latitude"),
                 query.get("lon") or query.get("longitude"),
                 query.get("timestamp") or query.get("ts"),
                 accuracy=query.get("accuracy") or query.get("hdop"),
                 altitude=query.get("altitude") or query.get("altitude_m"),
                 speed=query.get("speed"), course=query.get("bearing") or query.get("heading"),
                 battery=query.get("batt") or query.get("battery"),
                 quelle="traccar", roh=dict(query))
        return [p] if p else []

    # Flach — Home Assistant und alles Selbstgebaute.
    p = _fix(daten.get("lat", daten.get("latitude")),
             daten.get("lon", daten.get("lng", daten.get("longitude"))),
             daten.get("ts", daten.get("timestamp", daten.get("time"))),
             accuracy=daten.get("accuracy", daten.get("gps_accuracy", daten.get("acc"))),
             altitude=daten.get("altitude", daten.get("alt")),
             speed=daten.get("speed"),
             course=daten.get("course", daten.get("bearing", daten.get("heading"))),
             battery=daten.get("battery", daten.get("batt", daten.get("battery_level"))),
             quelle=str(daten.get("source") or "api"), roh=daten)
    return [p] if p else []


def _overland(eintraege: list) -> list[dict]:
    punkte = []
    for e in eintraege:
        if not isinstance(e, dict):
            continue
        koord = ((e.get("geometry") or {}).get("coordinates") or [])
        eig = e.get("properties") or {}
        if len(koord) < 2:
            continue
        # GeoJSON zaehlt lon zuerst. Wer das vertauscht, landet mitten im Indischen Ozean.
        p = _fix(koord[1], koord[0], eig.get("timestamp"),
                 accuracy=eig.get("horizontal_accuracy"), altitude=eig.get("altitude"),
                 speed=eig.get("speed"), course=eig.get("course"),
                 battery=eig.get("battery_level"), quelle="overland", roh=eig)
        if p:
            punkte.append(p)
    return punkte
