"""Benchmarks: solar position against physical invariants; geometry primitives."""
import math
from datetime import datetime, timezone
from backend.engine.solar import sun_position, solar_noon_utc, daylight_samples
from backend.engine import geometry as g

UTC = timezone.utc


def test_solstice_and_equinox_declination():
    assert abs(sun_position(datetime(2026, 6, 21, 12, tzinfo=UTC), 0, 0).declination - 23.44) < 0.03
    assert abs(sun_position(datetime(2026, 12, 21, 12, tzinfo=UTC), 0, 0).declination + 23.44) < 0.03
    assert abs(sun_position(datetime(2026, 3, 20, 14, tzinfo=UTC), 0, 0).declination) < 0.1


def test_equation_of_time_extremes():
    assert abs(sun_position(datetime(2026, 2, 11, 12, tzinfo=UTC), 0, 0).eot_min + 14.2) < 0.3
    assert abs(sun_position(datetime(2026, 11, 3, 12, tzinfo=UTC), 0, 0).eot_min - 16.4) < 0.3


def test_solar_noon_elevation_and_azimuth():
    for lat, lon, month in [(17.385, 78.487, 12), (40.0, -105.0, 6), (-33.9, 18.4, 6), (51.5, 0.0, 3)]:
        noon = solar_noon_utc(datetime(2026, month, 15, tzinfo=UTC), lon)
        sp = sun_position(noon, lat, lon)
        expected = 90 - abs(lat - sp.declination)
        assert abs(sp.elevation - expected) < 0.06, (lat, sp.elevation, expected)
        if lat - sp.declination > 1:
            assert abs(sp.azimuth - 180) < 0.5
        elif lat - sp.declination < -1:
            assert min(sp.azimuth, 360 - sp.azimuth) < 0.5


def test_sunrise_azimuth_east_at_equinox():
    s = daylight_samples(0.0, 0.0, step_min=30, min_elev=0.5)
    march = [sp for t, sp in s if t.month == 3]
    assert 85 < march[0].azimuth < 95


def test_daylight_sampling_is_longitude_independent():
    """v0.2 hard-coded UTC 03-10h; samples must now follow local solar time anywhere."""
    for lon in (-120, 0, 78.5, 150):
        s = daylight_samples(20.0, lon, step_min=60)
        assert len(s) > 100
        assert all(sp.elevation >= 5 for _, sp in s)


def test_geometry_primitives():
    sq = [(0, 0), (10, 0), (10, 10), (0, 10)]
    assert g.area(sq) == 100 and g.centroid(sq) == (5, 5)
    assert g.point_in_polygon((5, 5), sq) and not g.point_in_polygon((11, 5), sq)
    assert not g.is_simple([(0, 0), (2, 2), (0, 2), (2, 0)])          # bow-tie
    assert not g.is_simple([(0, 0), (1, 1), (2, 2)])                    # collinear
    assert abs(g.polygon_distance(sq, [(12, 0), (13, 0), (13, 1)]) - 2) < 1e-9
    assert g.polygon_distance(sq, [(5, 5), (6, 5), (6, 6)]) == 0
    L = [(0, 0), (10, 0), (10, 4), (4, 4), (4, 10), (0, 10)]          # concave
    assert not g.rect_within([(5, 5), (6, 5), (6, 6), (5, 6)], L, 0)   # in the notch
    assert g.rect_within([(1, 1), (2, 1), (2, 2), (1, 2)], L, 0.5)
    assert not g.rect_within([(3.8, 4.5), (3.9, 4.5), (3.9, 5), (3.8, 5)], L, 0.5)
