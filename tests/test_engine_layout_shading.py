import math
import numpy as np
from datetime import datetime, timezone
from backend.engine import geometry as g
from backend.engine.layout import LayoutSpec, Obstacle, place_modules, row_shade_gap
from backend.engine.shading import analyse, _wall_block, build_walls, shadow_polygons
from backend.engine.solar import sun_position, solar_noon_utc, sun_vector

UTC = timezone.utc


def _validate(spec, res):
    rects = [p["footprint"] for p in res["panels"]]
    roof = g.clean(spec.roof)
    for r in rects:
        assert g.rect_within(r, roof, spec.setback_m - 1e-6)
        for o in spec.obstacles:
            assert g.polygon_distance(r, o.polygon) >= spec.obstacle_clearance_m - 1e-6
    for i in range(len(rects)):
        for j in range(i + 1, len(rects)):
            a, b = rects[i], rects[j]
            if math.dist(g.centroid(a), g.centroid(b)) < 3:
                # allow shared edges only when gap is zero; never interior overlap
                ov = g.point_in_polygon(g.centroid(a), b) or g.point_in_polygon(g.centroid(b), a)
                assert not ov


def test_rectangular_roof_exact_count():
    spec = LayoutSpec(roof=[(0, 0), (10, 0), (10, 10), (0, 10)], module_length_m=2, module_width_m=1,
                      setback_m=0, module_gap_m=0, min_row_gap_m=0, spacing_rule="fixed")
    assert len(place_modules(spec)["panels"]) == 50


def test_irregular_roof_constraints_respected():
    roof = [(0, 0), (40, 0), (40, 12), (25, 12), (25, 25), (0, 25)]
    obs = [Obstacle(polygon=[(5, 5), (8, 5), (8, 8), (5, 8)], height_m=2.5),
           Obstacle(polygon=[(30, 3), (33, 3), (31.5, 7)], height_m=1.2)]
    for az, orient, tilt in [(180, "portrait", 0), (200, "landscape", 10), (135, "portrait", 20)]:
        spec = LayoutSpec(roof=roof, obstacles=obs, array_azimuth_deg=az, orientation=orient,
                          tilt_deg=tilt, setback_m=1.0, obstacle_clearance_m=0.8, optimize_offsets=2)
        res = place_modules(spec)
        assert len(res["panels"]) > 40
        _validate(spec, res)


def test_row_gap_matches_solstice_geometry():
    spec = LayoutSpec(roof=[(0, 0), (1, 0), (1, 1)], tilt_deg=20, latitude=17.385, longitude=78.487)
    gap, info = row_shade_gap(spec, 2.278)
    h = 2.278 * math.sin(math.radians(20))
    noon = solar_noon_utc(datetime(2026, 12, 21, tzinfo=UTC), 78.487)
    e = sun_position(noon, 17.385, 78.487).elevation
    assert gap >= h / math.tan(math.radians(e)) - 1e-6          # at least the noon shadow
    assert gap < h / math.tan(math.radians(20))                  # less than an unrealistic 20 deg sun
    assert row_shade_gap(LayoutSpec(roof=[], tilt_deg=0), 2.278)[0] == 0


def test_wall_shadow_length_analytic():
    """Wall 2 m high running E-W; Hyderabad solar noon, Dec 21: shadow = H / tan(elev)."""
    lat, lon = 17.385, 78.487
    noon = solar_noon_utc(datetime(2026, 12, 21, tzinfo=UTC), lon)
    sp = sun_position(noon, lat, lon)
    L = 2.0 / math.tan(math.radians(sp.elevation))
    walls, h, t = build_walls([], [([(-50, 0), (50, 0), (50, -0.2), (-50, -0.2)], 2.0, 0.0)], 0)
    pts = np.array([[0, L - 0.05, 0.0], [0, L + 0.05, 0.0], [0, -5, 0.0]])
    lit = _wall_block(pts, sun_vector(sp.elevation, sp.azimuth), walls, h, t)
    assert lit.tolist() == [0.0, 1.0, 1.0]
    poly = shadow_polygons([{"polygon": [(-1, 0), (1, 0), (1, -0.2), (-1, -0.2)], "height_m": 2.0}],
                           sp.elevation, sp.azimuth)[0]["polygon"]
    assert abs(max(y for _, y in poly) - L) < 0.01


def _panels(spec):
    return place_modules(spec)["panels"]


def test_shading_direction_and_height_monotonic():
    roof = [(0, 0), (20, 0), (20, 20), (0, 20)]
    base = dict(roof=roof, setback_m=0.5, obstacle_clearance_m=0.5, latitude=40.0, longitude=-105.0)
    south_obs = [(8, 1), (12, 1), (12, 3), (8, 3)]
    north_obs = [(8, 17), (12, 17), (12, 19), (8, 19)]
    res = {}
    for name, poly, h in [("south2", south_obs, 2), ("south4", south_obs, 4), ("north4", north_obs, 4)]:
        spec = LayoutSpec(obstacles=[Obstacle(polygon=poly, height_m=h)], **base)
        p = _panels(spec)
        res[name] = analyse(p, roof, [{"polygon": poly, "height_m": h}], 40.0, -105.0, step_min=60,
                            nu=2, nv=2)["beam_weighted_shading_pct"]
    assert res["south4"] > res["south2"] > 0
    assert res["north4"] < 0.25 * res["south4"]


def test_tree_transmittance_between_clear_and_opaque():
    roof = [(0, 0), (20, 0), (20, 20), (0, 20)]
    tree = [(8, 1), (12, 1), (12, 3), (8, 3)]
    p = _panels(LayoutSpec(roof=roof, obstacles=[Obstacle(polygon=tree, height_m=4)], latitude=17.4))
    opaque = analyse(p, roof, [{"polygon": tree, "height_m": 4}], 17.4, 78.5, nu=2, nv=2)["beam_weighted_shading_pct"]
    half = analyse(p, roof, [{"polygon": tree, "height_m": 4, "transmittance": 0.5}], 17.4, 78.5,
                   nu=2, nv=2)["beam_weighted_shading_pct"]
    assert abs(half - opaque / 2) < 1e-6 and half > 0


def test_row_to_row_shading_and_parapet():
    roof = [(0, 0), (20, 0), (20, 15), (0, 15)]
    tight = LayoutSpec(roof=roof, tilt_deg=25, spacing_rule="fixed", min_row_gap_m=0.1, latitude=28.6, longitude=77.2)
    spaced = LayoutSpec(roof=roof, tilt_deg=25, latitude=28.6, longitude=77.2)
    s_tight = analyse(_panels(tight), roof, [], 28.6, 77.2, nu=2, nv=3)["beam_weighted_shading_pct"]
    s_spaced = analyse(_panels(spaced), roof, [], 28.6, 77.2, nu=2, nv=3)["beam_weighted_shading_pct"]
    assert s_tight > 2 * s_spaced and s_tight > 1.0
    flat = _panels(LayoutSpec(roof=roof, setback_m=0.3))
    assert analyse(flat, roof, [], 28.6, 77.2, nu=2, nv=2)["beam_weighted_shading_pct"] == 0
    assert analyse(flat, roof, [], 28.6, 77.2, parapet_height_m=1.2, nu=2, nv=2)["beam_weighted_shading_pct"] > 0
