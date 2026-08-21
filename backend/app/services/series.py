"""Punkte aufnehmen — und merken, wenn dabei ein Ort betreten oder verlassen wird.

Der Aufbau folgt `services/metrics.erfassen`: eine duenne Schicht, die schreibt und den
denormalisierten Stand nachzieht. Drei Dinge kommen bei Standorten dazu, und jedes davon hat
einen handfesten Grund:

1. **Verwerfen statt Werfen.** Ein Telefon meldet auch mal eine Position mit 2 km
   Ungenauigkeit oder einen Zeitstempel aus der Zukunft. Das ist kein Fehler des Absenders,
   sondern der Alltag von GPS — der Aufruf gelingt, der Punkt faellt weg.
2. **Ruhefilter.** Ohne ihn waechst die Tabelle nachts am Schreibtisch genauso schnell wie
   auf der Autobahn: Ein Geraet, das alle vier Minuten dieselbe Koordinate schickt, erzeugt
   375 Zeilen am Tag, die nichts erzaehlen.
3. **Geozaun.** Nach dem Schreiben wird verglichen, in welchen Orten das Geraet jetzt steht
   und in welchen vorher. Die Differenz wird als Ereignis gemeldet — und damit ist ein
   Standort das, wofuer er hier ueberhaupt gespeichert wird: ein Ausloeser wie eine Mail.
"""
from __future__ import annotations

import datetime as dt
import hashlib
import logging
import secrets

from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..core.security import encrypt_secret
from ..models.series import Series, SeriesPlace, SeriesPoint, SeriesShare
from . import geo
from .events import emit

log = logging.getLogger("series")

# Vorgaben eines Standort-Geraets. Sie stehen in `Series.settings` und lassen sich je Reihe
# aendern; ein Auto darf enger takten als ein Telefon.
VORGABEN_LOCATION = {
    "min_distance_m": 25,     # Naeher am letzten Punkt: nicht schreiben …
    "min_interval_s": 300,    # … es sei denn, es ist so lange her.
    "max_accuracy_m": 500,    # Ungenauer: verwerfen.
}
# Erst so weit draussen gilt ein Ort als verlassen. Ohne diesen Zuschlag flattert ein Geraet
# am Rand eines Zauns zwischen "drin" und "draussen" und loest jedesmal einen Ablauf aus.
HYSTERESIS_M = 50
# Weiter als das in der Zukunft ist kein Zeitstempel, sondern eine kaputte Uhr.
ZUKUNFT_TOLERANZ = dt.timedelta(minutes=5)


def _now() -> dt.datetime:
    return dt.datetime.now(tz=dt.timezone.utc)


def _mit_zone(value: dt.datetime | None) -> dt.datetime | None:
    """Naive Zeitstempel als UTC lesen (SQLite in den Tests liefert sie ohne Zone)."""
    if value is None or value.tzinfo:
        return value
    return value.replace(tzinfo=dt.timezone.utc)


def setting(reihe: Series, name: str) -> int:
    return int((reihe.settings or {}).get(name, VORGABEN_LOCATION.get(name, 0)))


# ── Token ────────────────────────────────────────────────────────────────────

def token_hash(roh: str) -> str:
    """Der Suchschluessel zu einem Token.

    sha256 statt eines Vergleichs ueber alle Reihen: Bei zwei Geraeten waere beides gleich
    schnell, aber der Aufnahmepfad ist der eine, der oefter laeuft als alles andere im Haus.
    """
    return hashlib.sha256(roh.encode()).hexdigest()


def neuer_token(reihe: Series) -> str:
    """Ein frisches Token setzen und im Klartext zurueckgeben — einmalig."""
    roh = "trk_" + secrets.token_urlsafe(24)
    reihe.token_hash = token_hash(roh)
    reihe.token_enc = encrypt_secret(roh)
    return roh


# ── Sichtbarkeit ─────────────────────────────────────────────────────────────

def visible(user_id: int, ist_admin: bool = False):
    """Filter fuer Reihen, die dieser Mensch sehen darf: eigene, geteilte, globale."""
    if ist_admin:
        return True
    geteilt = select(SeriesShare.series_id).where(SeriesShare.user_id == user_id)
    return or_(Series.owner_user_id == user_id, Series.owner_user_id.is_(None),
               Series.id.in_(geteilt))


async def may_update(db: AsyncSession, reihe: Series, user_id: int,
                       ist_admin: bool = False) -> bool:
    if ist_admin or reihe.owner_user_id == user_id:
        return True
    grant = (await db.execute(select(SeriesShare).where(
        SeriesShare.series_id == reihe.id, SeriesShare.user_id == user_id))).scalar_one_or_none()
    return bool(grant and grant.level == "manage")


# ── Finden und Anlegen ───────────────────────────────────────────────────────

async def reihe(db: AsyncSession, owner_id: int | None, key: str, *, kind: str = "location",
                create: bool = False, name: str = "", farbe: str = "") -> Series | None:
    """Eine Reihe holen — auf Wunsch mit Anlegen beim ersten Punkt.

    Wie bei den Messreihen: Ein Ablauf soll eine Reihe nicht erst von Hand angelegt bekommen
    muessen. Wer eine Position wegschreibt, meint damit auch, dass es diese Reihe geben soll.
    """
    r = (await db.execute(select(Series).where(
        Series.owner_user_id == owner_id, Series.key == key))).scalar_one_or_none()
    if r is None and create:
        r = Series(owner_user_id=owner_id, key=key, kind=kind, name=name or key, color=farbe)
        db.add(r)
        await db.flush()
    return r


# ── Aufnehmen ────────────────────────────────────────────────────────────────

async def ingest(db: AsyncSession, reihe: Series, points: list[dict],
                    source: str = "") -> dict:
    """Punkte anhaengen — welche Art, entscheidet die Reihe.

    Das Anhaengen selbst ist fuer alle Arten dasselbe: Zeitstempel, Werte, Stand nachziehen.
    Verschieden ist nur, was einen Punkt ausmacht (eine Zahl, ein Ort, ein Text) und was
    dabei zusaetzlich passiert — der Ruhefilter und die Geozaeune gibt es nur bei Standorten,
    weil nur dort dieselbe Angabe hundertmal hintereinander kommt.
    """
    if reihe.kind == "location":
        return await _locations_ingest(db, reihe, points, source)
    return await _values_ingest(db, reihe, points, source)


async def _values_ingest(db: AsyncSession, reihe: Series, points: list[dict],
                           source: str) -> dict:
    """Zahlen und Texte: schreiben, Stand nachziehen, bei Texten aufraeumen.

    Kein Ruhefilter: Ein Messwert, der sich nicht geaendert hat, ist trotzdem eine Aussage
    ("das Geraet lebt und meldet 22 %"), und ein Text ist ohnehin jedesmal ein neuer.
    """
    grenzen = reihe.settings or {}
    unten, oben = grenzen.get("min"), grenzen.get("max")
    written, verworfen = 0, 0
    last, last_ts = None, None

    for p in sorted(points, key=lambda x: x.get("ts") or _now()):
        ts = _mit_zone(p.get("ts")) or _now()
        entry = SeriesPoint(series_id=reihe.id, ts=ts, source=p.get("source") or source,
                              context=p.get("context") or {})
        if reihe.kind == "number":
            value = p.get("value")
            if value is None:
                verworfen += 1
                continue
            # Plausibilitaetsgrenzen wie beim Messwert: Geraete melden Unsinn, wenn sie
            # etwas nicht wissen — und ein Ausreisser verzieht jede Auswertung danach.
            if (unten is not None and value < unten) or (oben is not None and value > oben):
                verworfen += 1
                continue
            entry.value = float(value)
        else:
            text = str(p.get("body") or "")
            if not text.strip():
                verworfen += 1
                continue
            entry.title = str(p.get("title") or "")[:200]
            entry.body = text
            entry.format = str(p.get("format") or "markdown")
        db.add(entry)
        written += 1
        last, last_ts = entry, ts

    reihe.points = (reihe.points or 0) + written
    if last is not None:
        bisher = _mit_zone(reihe.last_at)
        if bisher is None or last_ts >= bisher:
            state = dict(reihe.state or {})
            if reihe.kind == "number":
                state["value"] = last.value
            else:
                state["title"] = last.title
            reihe.state = state
            reihe.last_at = last_ts
            reihe.still_at = None
        await _prune(db, reihe)

    return {"accepted": written, "skipped": verworfen, "still": 0,
            "betreten": [], "verlassen": []}


async def _prune(db: AsyncSession, reihe: Series) -> None:
    """Alte Eintraege wegwerfen, wenn die Reihe eine Obergrenze nennt.

    Nur nach Anzahl und nur, wenn `keep` gesetzt ist. Eine Zeitgrenze waere bei Standorten
    das Falsche (ein Jahr Spur ist der Sinn der Sache), und ohne Angabe wird nichts geloescht
    — stilles Verschwinden von Daten will niemand geerbt bekommen.
    """
    limit = int((reihe.settings or {}).get("keep") or 0)
    if limit <= 0:
        return
    alte = (await db.execute(select(SeriesPoint).where(SeriesPoint.series_id == reihe.id)
                             .order_by(SeriesPoint.id.desc()).offset(limit))).scalars().all()
    for e in alte:
        await db.delete(e)
    if alte:
        reihe.points = max(0, (reihe.points or 0) - len(alte))


async def _locations_ingest(db: AsyncSession, reihe: Series, points: list[dict],
                               source: str = "") -> dict:
    """Standorte: mit Ruhefilter und Geozaun."""
    genau_genug = setting(reihe, "max_accuracy_m")
    min_m = setting(reihe, "min_distance_m")
    min_s = setting(reihe, "min_interval_s")
    zukunft = _now() + ZUKUNFT_TOLERANZ

    state = dict(reihe.state or {})
    last_ts = _mit_zone(reihe.last_at)
    last_lat, last_lon = state.get("lat"), state.get("lon")
    written, verworfen, geruht = 0, 0, 0
    last_point = None
    # Die zuletzt *gesehene* gueltige Position — unabhaengig davon, ob sie auch gespeichert
    # wurde. Der Ruhefilter entscheidet ueber das Speichern, nicht darueber, wo das Geraet
    # ist: Wer zu Fuss ueber eine Zaungrenze geht, legt in fuenf Minuten keine 25 m zurueck
    # und wuerde sonst erst viel spaeter als angekommen gelten.
    seen: tuple[float, float, dt.datetime] | None = None

    # Nach Zeit sortiert einspielen: Ein Stapel kommt nicht zwingend geordnet an, und der
    # Ruhefilter vergleicht immer mit dem zuletzt geschriebenen Punkt.
    for p in sorted(points, key=lambda x: x.get("ts") or _now()):
        lat, lon = p.get("lat"), p.get("lon")
        ts = _mit_zone(p.get("ts")) or _now()
        genauigkeit = (p.get("extra") or {}).get("accuracy")

        if lat is None or lon is None or (lat == 0 and lon == 0):
            verworfen += 1
            continue
        if not (-90 <= lat <= 90) or not (-180 <= lon <= 180):
            verworfen += 1
            continue
        if genau_genug and genauigkeit is not None and genauigkeit > genau_genug:
            verworfen += 1
            continue
        if ts > zukunft:
            verworfen += 1
            continue

        seen = (lat, lon, ts)

        # Ruhefilter: nah dran UND kurz her -> nicht schreiben.
        if last_lat is not None and last_lon is not None:
            weg = geo.distance_m(last_lat, last_lon, lat, lon)
            alt = (ts - last_ts).total_seconds() if last_ts else None
            if weg < min_m and (alt is None or 0 <= alt < min_s):
                geruht += 1
                continue

        punkt = SeriesPoint(
            series_id=reihe.id, ts=ts, lat=lat, lon=lon,
            extra=p.get("extra") or {}, source=p.get("source") or source,
            context={})
        db.add(punkt)
        written += 1
        last_point = punkt
        last_lat, last_lon, last_ts = lat, lon, ts

    reihe.points = (reihe.points or 0) + written

    orte = {"betreten": [], "verlassen": []}
    if last_point is not None:
        # Den Stand nur nachziehen, wenn der neue Punkt wirklich der neuere ist: Ein
        # Nachtrag aus dem Vorjahr darf nicht als aktuelle Position gelten.
        bisher = _mit_zone(reihe.last_at)
        if bisher is None or last_ts >= bisher:
            extra = last_point.extra or {}
            state = {**state, "lat": last_lat, "lon": last_lon,
                     "accuracy": extra.get("accuracy"), "battery": extra.get("battery"),
                     "speed": extra.get("speed")}
            reihe.state = state
            reihe.last_at = last_ts
            reihe.still_at = None

    if seen is not None:
        lat, lon, ts = seen
        # `seen_at` steht neben `last_at`: gemeldet hat sich das Geraet gerade, gespeichert
        # wurde vielleicht seit einer Stunde nichts. Beides ist eine eigene Auskunft — die
        # Karte zeigt den letzten Punkt, die Ueberwachung will wissen, ob noch etwas kommt.
        reihe.state = {**state, "seen_at": ts.isoformat()}
        reihe.still_at = None
        bisher = _mit_zone(reihe.last_at)
        # Zaeune nur an der neuesten bekannten Stelle pruefen: Ein Nachtrag von gestern darf
        # nicht melden, dass man gerade angekommen sei.
        if bisher is None or ts >= bisher:
            orte = await _fences_check(db, reihe, lat, lon, ts)

    return {"accepted": written, "skipped": verworfen, "still": geruht, **orte}


async def _fences_check(db: AsyncSession, reihe: Series, lat: float, lon: float,
                          ts: dt.datetime) -> dict:
    """Welche Orte sind dazugekommen, welche verlassen — und Ereignisse dafuer melden."""
    orte = (await db.execute(select(SeriesPlace).where(
        SeriesPlace.owner_user_id == reihe.owner_user_id,
        or_(SeriesPlace.series_id.is_(None), SeriesPlace.series_id == reihe.id),
    ))).scalars().all()
    if not orte:
        return {"betreten": [], "verlassen": []}

    vorher = set((reihe.state or {}).get("places") or [])
    now: set[str] = set()
    for ort in orte:
        weg = geo.distance_m(lat, lon, ort.lat, ort.lon)
        # Hinein ab dem Radius, hinaus erst spaeter — sonst flattert es am Rand.
        drin = weg <= ort.radius_m if ort.key not in vorher else weg <= ort.radius_m + HYSTERESIS_M
        if drin:
            now.add(ort.key)

    betreten = [o for o in orte if o.key in now - vorher]
    verlassen = [o for o in orte if o.key in vorher - now]
    reihe.state = {**(reihe.state or {}), "places": sorted(now)}

    for ort, name in ((o, "series.enter") for o in betreten):
        await _report(db, reihe, ort, name, lat, lon, ts)
    for ort, name in ((o, "series.leave") for o in verlassen):
        await _report(db, reihe, ort, name, lat, lon, ts)

    return {"betreten": [o.key for o in betreten], "verlassen": [o.key for o in verlassen]}


async def _report(db: AsyncSession, reihe: Series, ort: SeriesPlace, ereignis: str,
                  lat: float, lon: float, ts: dt.datetime) -> None:
    """Ein Ereignis abgeben — ohne dass ein Fehler dabei die Aufnahme umwirft.

    Ein Punkt ist erst einmal ein Punkt. Wenn ein Ablauf daran scheitert, soll trotzdem in
    der Datenbank stehen, wo das Geraet war.
    """
    if not ort.notify:
        return
    try:
        await emit(db, ereignis, payload={
            "series": {"key": reihe.key, "name": reihe.name, "id": reihe.id},
            "place": {"key": ort.key, "name": ort.name or ort.key,
                      "lat": ort.lat, "lon": ort.lon, "radius_m": ort.radius_m},
            "lat": lat, "lon": lon, "ts": ts.isoformat(),
        }, actor_id=reihe.owner_user_id)
    except Exception as exc:  # noqa: BLE001
        log.warning("Ereignis %s fuer %s/%s nicht gemeldet: %s",
                    ereignis, reihe.key, ort.key, exc)
