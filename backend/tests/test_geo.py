"""Die Kugelrechnung, an Strecken gemessen, die man nachschlagen kann."""
import math

from app.services.geo import distance_m, rahmen


def test_a_known_distance():
    """Hamburg -> Muenchen sind rund 612 km Luftlinie."""
    m = distance_m(53.5511, 9.9937, 48.1351, 11.5820)
    assert 605_000 < m < 620_000


def test_a_short_distance_is_right_to_the_metre():
    """Ein Zehntel Grad Breite ist ueberall gleich lang: gut 11,1 km."""
    assert abs(distance_m(50.0, 10.0, 50.1, 10.0) - 11_119) < 30


def test_the_same_point_is_zero():
    assert distance_m(50.0, 10.0, 50.0, 10.0) == 0.0


def test_the_bounding_box_encloses_the_radius():
    lat, lon, r = 50.0825, 10.5663, 150.0
    lat_min, lat_max, lon_min, lon_max = rahmen(lat, lon, r)
    # Die Kanten liegen mindestens so weit weg wie der Radius — sonst schnitte die
    # Vorauswahl Punkte ab, die drin liegen.
    assert distance_m(lat, lon, lat_max, lon) >= r
    assert distance_m(lat, lon, lat, lon_max) >= r
    assert lat_min < lat < lat_max and lon_min < lon < lon_max


def test_the_bounding_box_does_not_degenerate_at_the_pole():
    _, _, lon_min, lon_max = rahmen(90.0, 0.0, 1000.0)
    assert lon_min == -180.0 and lon_max == 180.0
    assert all(math.isfinite(w) for w in rahmen(89.999, 0.0, 1000.0))
