"""The four languages of a location report, each measured against its own trap."""
import datetime as dt

from app.services.series_formats import normalise, moment


def test_owntracks():
    p, = normalise({"_type": "location", "lat": 50.08, "lon": 10.56, "tst": 1755700000,
                       "acc": 12, "batt": 76, "alt": 322, "vel": 5})
    assert (p["lat"], p["lon"]) == (50.08, 10.56)
    assert p["extra"] == {"accuracy": 12.0, "altitude": 322.0, "speed": 5.0, "battery": 76.0}
    assert p["source"] == "owntracks"
    assert p["ts"] == dt.datetime.fromtimestamp(1755700000, tz=dt.timezone.utc)


def test_owntracks_ignores_what_is_not_a_location():
    """Wegpunkte und Statusnachrichten kommen ueber dieselbe Adresse."""
    assert normalise({"_type": "waypoint", "lat": 50.0, "lon": 10.0}) == []


def test_an_overland_batch_and_the_swapped_order():
    """GeoJSON zaehlt lon zuerst — wer das dreht, landet im Indischen Ozean."""
    points = normalise({"locations": [
        {"geometry": {"coordinates": [10.5663, 50.0825]},
         "properties": {"timestamp": "2026-08-20T12:00:00Z", "battery_level": 0.42,
                        "horizontal_accuracy": 8}},
        {"geometry": {"coordinates": [10.5700, 50.0900]}, "properties": {}},
    ]})
    assert len(points) == 2
    assert points[0]["lat"] == 50.0825 and points[0]["lon"] == 10.5663
    # 0.42 is a fraction, not 0.42 percent.
    assert points[0]["extra"]["battery"] == 42.0
    assert points[0]["source"] == "overland"


def test_traccar_arrives_through_the_address():
    points = normalise({}, {"id": "handy", "lat": "50.08", "lon": "10.56",
                               "timestamp": "1755700000", "speed": "12.5",
                               "batt": "88", "accuracy": "9", "bearing": "180"})
    assert len(points) == 1
    p = points[0]
    assert (p["lat"], p["lon"], p["source"]) == (50.08, 10.56, "traccar")
    assert p["extra"]["course"] == 180.0 and p["extra"]["speed"] == 12.5


def test_flat_as_home_assistant_sends_it():
    p, = normalise({"latitude": 50.0825308, "longitude": 10.5663527,
                       "gps_accuracy": 16, "altitude": 322, "battery": 63,
                       "ts": "2026-08-20T16:25:54+00:00", "source": "ha"})
    assert p["lat"] == 50.0825308
    assert p["extra"]["accuracy"] == 16.0 and p["extra"]["battery"] == 63.0
    assert p["source"] == "ha"


def test_without_a_coordinate_nothing_arrives():
    assert normalise({"battery": 50}) == []
    assert normalise({"_type": "location", "batt": 50}) == []
    assert normalise("kein Objekt") == []
    assert normalise({"locations": [{"geometry": {"coordinates": [10.0]}}]}) == []


def test_numbers_also_arrive_contaminated():
    p, = normalise({"lat": "50,08", "lon": " 10.56 ", "speed": "12,5 km/h"})
    assert p["lat"] == 50.08 and p["lon"] == 10.56
    assert p["extra"]["speed"] == 12.5


def test_timestamps_in_all_common_shapes():
    should = dt.datetime(2026, 8, 20, 12, 0, tzinfo=dt.timezone.utc)
    assert moment(1787227200) == should
    assert moment(1787227200000) == should        # Millisekunden
    assert moment("2026-08-20T12:00:00Z") == should
    assert moment("2026-08-20T14:00:00+02:00") == should
    # Without a zone UTC applies — otherwise the result hangs on the zone of the server.
    assert moment("2026-08-20T12:00:00") == should
    assert moment("") is None and moment(None) is None and moment("gestern") is None


def test_battery_as_a_fraction_and_as_a_percentage():
    """The mistake that produced 8200 percent on the way to dawarich."""
    assert normalise({"lat": 1, "lon": 1, "battery": 0.82})[0]["extra"]["battery"] == 82.0
    assert normalise({"lat": 1, "lon": 1, "battery": 82})[0]["extra"]["battery"] == 82.0
    assert normalise({"lat": 1, "lon": 1, "battery": 8200})[0]["extra"]["battery"] == 100.0
