"""Die Kugelrechnung, an Strecken gemessen, die man nachschlagen kann."""
import math

from app.services.geo import abstand_m, rahmen


def test_bekannte_strecke():
    """Hamburg -> Muenchen sind rund 612 km Luftlinie."""
    m = abstand_m(53.5511, 9.9937, 48.1351, 11.5820)
    assert 605_000 < m < 620_000


def test_kurze_strecke_stimmt_auf_meter():
    """Ein Zehntel Grad Breite ist ueberall gleich lang: gut 11,1 km."""
    assert abs(abstand_m(50.0, 10.0, 50.1, 10.0) - 11_119) < 30


def test_gleicher_punkt_ist_null():
    assert abstand_m(50.0, 10.0, 50.0, 10.0) == 0.0


def test_rahmen_umschliesst_den_radius():
    lat, lon, r = 50.0825, 10.5663, 150.0
    lat_min, lat_max, lon_min, lon_max = rahmen(lat, lon, r)
    # Die Kanten liegen mindestens so weit weg wie der Radius — sonst schnitte die
    # Vorauswahl Punkte ab, die drin liegen.
    assert abstand_m(lat, lon, lat_max, lon) >= r
    assert abstand_m(lat, lon, lat, lon_max) >= r
    assert lat_min < lat < lat_max and lon_min < lon < lon_max


def test_rahmen_am_pol_entartet_nicht():
    _, _, lon_min, lon_max = rahmen(90.0, 0.0, 1000.0)
    assert lon_min == -180.0 and lon_max == 180.0
    assert all(math.isfinite(w) for w in rahmen(89.999, 0.0, 1000.0))
