"""Automatic module placement on irregular roofs.

Every placed module footprint is guaranteed (and tested) to:
  * lie inside the roof polygon with >= setback to every roof edge (incl. concave corners),
  * keep >= clearance from every obstacle / keep-out zone,
  * not overlap any other module.
Row pitch for tilted arrays is derived from a no-shade window on the winter
solstice (09:00-15:00 local solar time) unless the user forces a larger gap.
"""
from __future__ import annotations
import math
from dataclasses import dataclass, field
from datetime import datetime, timezone, timedelta
from . import geometry as g
from .solar import sun_position, solar_noon_utc


@dataclass
class Obstacle:
    polygon: list
    height_m: float = 1.0
    name: str = "Obstacle"
    kind: str = "obstacle"      # obstacle | keepout | tree | building
    clearance_m: float | None = None  # overrides global clearance


@dataclass
class LayoutSpec:
    roof: list
    obstacles: list = field(default_factory=list)
    module_length_m: float = 2.278
    module_width_m: float = 1.134
    orientation: str = "portrait"      # portrait: long side up-slope
    tilt_deg: float = 0.0
    array_azimuth_deg: float = 180.0   # direction modules face (180 = south)
    setback_m: float = 0.5
    obstacle_clearance_m: float = 0.6
    module_gap_m: float = 0.02         # between modules in a row (clamp gap)
    min_row_gap_m: float = 0.25
    mount_height_m: float = 0.3        # lower edge above roof surface
    latitude: float = 17.385
    longitude: float = 78.487
    spacing_rule: str = "solstice_9_15"   # or "fixed"
    optimize_offsets: int = 3          # n x n grid-phase trials


def _axes(az_deg: float):
    a = math.radians(az_deg)
    f = (math.sin(a), math.cos(a))          # facing direction (horizontal)
    r = (-f[1], f[0])                       # along-row direction
    return r, f


def row_shade_gap(spec: LayoutSpec, slope_len: float) -> tuple[float, dict]:
    """Minimum clear gap between rows so the row in front casts no shadow on
    the next row between 09:00 and 15:00 solar time on the winter solstice."""
    h = slope_len * math.sin(math.radians(spec.tilt_deg))
    if h <= 1e-6:
        return 0.0, {"rule": "flat modules: no inter-row shading gap needed"}
    month = 12 if spec.latitude >= 0 else 6
    noon = solar_noon_utc(datetime(2026, month, 21, tzinfo=timezone.utc), spec.longitude)
    worst, worst_t = 0.0, None
    for k in range(-180, 181, 15):
        sp = sun_position(noon + timedelta(minutes=k), spec.latitude, spec.longitude)
        if sp.elevation <= 2:
            continue
        cos_d = math.cos(math.radians(sp.azimuth - spec.array_azimuth_deg))
        if cos_d <= 0:
            continue
        gap = h * cos_d / math.tan(math.radians(sp.elevation))
        if gap > worst:
            worst, worst_t = gap, (k, round(sp.elevation, 2), round(sp.azimuth, 2))
    return worst, {"rule": "no row-to-row shading 09:00-15:00 solar time, winter solstice",
                   "back_edge_rise_m": round(h, 3),
                   "critical_minutes_from_noon": worst_t[0] if worst_t else None,
                   "critical_sun_elevation_deg": worst_t[1] if worst_t else None,
                   "critical_sun_azimuth_deg": worst_t[2] if worst_t else None}


def place_modules(spec: LayoutSpec) -> dict:
    roof = g.clean(spec.roof)
    if not g.is_simple(roof):
        raise ValueError("Roof polygon is invalid or self-intersecting")
    obs = []
    for o in spec.obstacles:
        poly = g.clean(o.polygon)
        if not g.is_simple(poly):
            raise ValueError(f"Obstacle '{o.name}' polygon is invalid")
        clr = spec.obstacle_clearance_m if o.clearance_m is None else o.clearance_m
        obs.append((poly, clr, g.bounds(poly)))

    if spec.orientation == "portrait":
        along, slope = spec.module_width_m, spec.module_length_m
    elif spec.orientation == "landscape":
        along, slope = spec.module_length_m, spec.module_width_m
    else:
        raise ValueError("orientation must be portrait or landscape")
    tilt = math.radians(spec.tilt_deg)
    depth = slope * math.cos(tilt)
    shade_gap, gap_info = (0.0, {"rule": "fixed user gap"})
    if spec.spacing_rule == "solstice_9_15":
        shade_gap, gap_info = row_shade_gap(spec, slope)
    gap = max(spec.min_row_gap_m, shade_gap)
    pitch_v = depth + gap
    pitch_u = along + spec.module_gap_m

    r, f = _axes(spec.array_azimuth_deg)
    to_uv = lambda p: (p[0] * r[0] + p[1] * r[1], p[0] * f[0] + p[1] * f[1])
    to_xy = lambda u, v: (u * r[0] + v * f[0], u * r[1] + v * f[1])
    uv = [to_uv(p) for p in roof]
    umin, vmin, umax, vmax = g.bounds(uv)
    est = (umax - umin) / pitch_u * (vmax - vmin) / pitch_v
    if est > 60000:
        raise ValueError("Roof too large for a single layout block; split into zones")

    def run(du, dv):
        placed = []
        rows = 0
        v = vmin + spec.setback_m + dv
        row = 0
        while v + depth <= vmax + 1e-9:
            u = umin + spec.setback_m + du
            col = 0
            any_in_row = False
            while u + along <= umax + 1e-9:
                rect = [to_xy(u, v), to_xy(u + along, v), to_xy(u + along, v + depth), to_xy(u, v + depth)]
                if g.rect_within(rect, roof, spec.setback_m):
                    rb = g.bounds(rect)
                    ok = True
                    for poly, clr, ob in obs:
                        if rb[0] > ob[2] + clr or rb[2] < ob[0] - clr or rb[1] > ob[3] + clr or rb[3] < ob[1] - clr:
                            continue
                        if g.polygon_distance(rect, poly) < clr - 1e-9 or (clr <= 0 and g.polygons_intersect(rect, poly)):
                            ok = False
                            break
                    if ok:
                        placed.append({"row": row, "col": col, "u": u, "v": v, "footprint": rect})
                        any_in_row = True
                u += pitch_u
                col += 1
            rows += any_in_row
            v += pitch_v
            row += 1
        return placed

    n = max(1, spec.optimize_offsets)
    best = None
    for i in range(n):
        for j in range(n):
            res = run(pitch_u * i / n, pitch_v * j / n)
            if best is None or len(res) > len(best):
                best = res
    panels = best or []
    # 3-D geometry. Front (low) edge is at larger v (toward facing direction).
    h_lo = spec.mount_height_m
    h_hi = h_lo + slope * math.sin(tilt)
    for i, p in enumerate(panels):
        u, v = p["u"], p["v"]
        back_l, back_r = to_xy(u, v), to_xy(u + along, v)
        front_r, front_l = to_xy(u + along, v + depth), to_xy(u, v + depth)
        p["id"] = f"M{i + 1:04d}"
        p["corners3d"] = [(*back_l, h_hi), (*back_r, h_hi), (*front_r, h_lo), (*front_l, h_lo)]
        p["center"] = g.centroid(p["footprint"])
        del p["u"], p["v"]
    # contiguous segments per row -> used for rails & clamps
    segments = []
    by_row: dict[int, list] = {}
    for p in panels:
        by_row.setdefault(p["row"], []).append(p["col"])
    for row, cols in sorted(by_row.items()):
        cols.sort()
        start = prev = cols[0]
        for c in cols[1:] + [None]:
            if c is not None and c == prev + 1:
                prev = c
                continue
            segments.append({"row": row, "first_col": start, "modules": prev - start + 1,
                             "length_m": round((prev - start + 1) * pitch_u - spec.module_gap_m, 3)})
            if c is not None:
                start = prev = c
    return {
        "panels": panels,
        "segments": segments,
        "rows": len(by_row),
        "pitch_along_row_m": round(pitch_u, 4),
        "row_pitch_m": round(pitch_v, 4),
        "row_gap_m": round(gap, 4),
        "row_gap_basis": gap_info,
        "module_footprint_depth_m": round(depth, 4),
        "module_top_edge_height_m": round(h_hi, 4),
        "roof_area_m2": round(g.area(roof), 3),
    }
