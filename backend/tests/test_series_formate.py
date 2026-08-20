"""Die vier Sprachen der Standortmeldung, jede an ihrer eigenen Falle gemessen."""
import datetime as dt

from app.services.series_formats import normalisiere, zeitpunkt


def test_owntracks():
    p, = normalisiere({"_type": "location", "lat": 50.08, "lon": 10.56, "tst": 1755700000,
                       "acc": 12, "batt": 76, "alt": 322, "vel": 5})
    assert (p["lat"], p["lon"]) == (50.08, 10.56)
    assert p["extra"] == {"accuracy": 12.0, "altitude": 322.0, "speed": 5.0, "battery": 76.0}
    assert p["source"] == "owntracks"
    assert p["ts"] == dt.datetime.fromtimestamp(1755700000, tz=dt.timezone.utc)


def test_owntracks_ignoriert_was_kein_standort_ist():
    """Wegpunkte und Statusnachrichten kommen ueber dieselbe Adresse."""
    assert normalisiere({"_type": "waypoint", "lat": 50.0, "lon": 10.0}) == []


def test_overland_stapel_und_die_vertauschte_reihenfolge():
    """GeoJSON zaehlt lon zuerst — wer das dreht, landet im Indischen Ozean."""
    punkte = normalisiere({"locations": [
        {"geometry": {"coordinates": [10.5663, 50.0825]},
         "properties": {"timestamp": "2026-08-20T12:00:00Z", "battery_level": 0.42,
                        "horizontal_accuracy": 8}},
        {"geometry": {"coordinates": [10.5700, 50.0900]}, "properties": {}},
    ]})
    assert len(punkte) == 2
    assert punkte[0]["lat"] == 50.0825 and punkte[0]["lon"] == 10.5663
    # 0,42 ist ein Bruchteil, keine 0,42 Prozent.
    assert punkte[0]["extra"]["battery"] == 42.0
    assert punkte[0]["source"] == "overland"


def test_traccar_kommt_ueber_die_adresse():
    punkte = normalisiere({}, {"id": "handy", "lat": "50.08", "lon": "10.56",
                               "timestamp": "1755700000", "speed": "12.5",
                               "batt": "88", "accuracy": "9", "bearing": "180"})
    assert len(punkte) == 1
    p = punkte[0]
    assert (p["lat"], p["lon"], p["source"]) == (50.08, 10.56, "traccar")
    assert p["extra"]["course"] == 180.0 and p["extra"]["speed"] == 12.5


def test_flach_wie_home_assistant_es_schickt():
    p, = normalisiere({"latitude": 50.0825308, "longitude": 10.5663527,
                       "gps_accuracy": 16, "altitude": 322, "battery": 63,
                       "ts": "2026-08-20T16:25:54+00:00", "source": "ha"})
    assert p["lat"] == 50.0825308
    assert p["extra"]["accuracy"] == 16.0 and p["extra"]["battery"] == 63.0
    assert p["source"] == "ha"


def test_ohne_koordinate_kommt_nichts():
    assert normalisiere({"battery": 50}) == []
    assert normalisiere({"_type": "location", "batt": 50}) == []
    assert normalisiere("kein Objekt") == []
    assert normalisiere({"locations": [{"geometry": {"coordinates": [10.0]}}]}) == []


def test_zahlen_kommen_auch_verunreinigt_an():
    p, = normalisiere({"lat": "50,08", "lon": " 10.56 ", "speed": "12,5 km/h"})
    assert p["lat"] == 50.08 and p["lon"] == 10.56
    assert p["extra"]["speed"] == 12.5


def test_zeitstempel_in_allen_gaengigen_formen():
    soll = dt.datetime(2026, 8, 20, 12, 0, tzinfo=dt.timezone.utc)
    assert zeitpunkt(1787227200) == soll
    assert zeitpunkt(1787227200000) == soll        # Millisekunden
    assert zeitpunkt("2026-08-20T12:00:00Z") == soll
    assert zeitpunkt("2026-08-20T14:00:00+02:00") == soll
    # Ohne Zone gilt UTC — sonst haengt das Ergebnis an der Zone des Servers.
    assert zeitpunkt("2026-08-20T12:00:00") == soll
    assert zeitpunkt("") is None and zeitpunkt(None) is None and zeitpunkt("gestern") is None


def test_akku_als_bruchteil_und_als_prozent():
    """Der Fehler, der beim Weg nach dawarich 8200 Prozent ergeben hat."""
    assert normalisiere({"lat": 1, "lon": 1, "battery": 0.82})[0]["extra"]["battery"] == 82.0
    assert normalisiere({"lat": 1, "lon": 1, "battery": 82})[0]["extra"]["battery"] == 82.0
    assert normalisiere({"lat": 1, "lon": 1, "battery": 8200})[0]["extra"]["battery"] == 100.0
