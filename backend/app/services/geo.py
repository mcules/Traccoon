"""Rechnen auf der Kugel — so wenig, wie fuer Geozaeune und Karten noetig ist.

PostGIS steht nicht zur Verfuegung: Das Abbild `postgres:16-alpine` bringt die Erweiterung
nicht mit, und die Tests laufen gegen SQLite. Fuer die Fragen, die hier gestellt werden — wie
weit ist dieser Punkt vom letzten entfernt, steht er in diesem Kreis — reicht die
Haversine-Formel in Python vollkommen: Bei einer Handvoll Orten je Mensch ist die Schleife
darueber schneller, als eine Datenbank die Anfrage entgegennehmen koennte.
"""
from __future__ import annotations

import math

# Mittlerer Erdradius (IUGG). Der Fehler gegenueber dem echten Ellipsoid liegt bei 0,3 % —
# bei einem Zaun mit 150 m Radius sind das 45 cm, und das GPS eines Telefons streut um das
# Zwanzigfache davon.
ERDRADIUS_M = 6_371_008.8


def distance_m(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Entfernung zweier Punkte in Metern."""
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp = p2 - p1
    dl = math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * ERDRADIUS_M * math.asin(min(1.0, math.sqrt(a)))


def rahmen(lat: float, lon: float, meter: float) -> tuple[float, float, float, float]:
    """Das Rechteck um einen Punkt: (lat_min, lat_max, lon_min, lon_max).

    Fuer die Vorauswahl in SQL, wenn einmal mehr Punkte da sind, als man durchgehen mag. Die
    Laengengrade ruecken zu den Polen hin zusammen, deshalb der Kosinus; direkt am Pol
    entartet die Rechnung, dort wird das Rechteck auf die ganze Breite geoeffnet.
    """
    # Ein Hauch mehr, als gefragt war. Der Rahmen ist eine Vorauswahl; waere er auf den
    # Meter genau, schnitte er wegen der Fliesskomma-Rundung gelegentlich genau den Punkt ab,
    # der noch drin liegt. Ein Promille plus ein Meter kostet nichts und verhindert das.
    meter = meter * 1.001 + 1.0
    d_lat = math.degrees(meter / ERDRADIUS_M)
    kos = math.cos(math.radians(lat))
    d_lon = 180.0 if abs(kos) < 1e-9 else math.degrees(meter / (ERDRADIUS_M * abs(kos)))
    return (lat - d_lat, lat + d_lat, lon - d_lon, lon + d_lon)
