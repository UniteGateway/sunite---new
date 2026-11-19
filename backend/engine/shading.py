"""3-D geometric shading engine (ray casting, numpy-vectorised).

Occluders
  * walls: vertical segments with a height above the roof plane. Obstacle prisms,
    adjacent buildings, trees and roof parapets are all expressed as walls. A ray
    leaving a sample point rises monotonically, so it is blocked iff it crosses any
    wall below that wall's height.
  * module surfaces: each tilted module is two triangles (row-to-row shading).

Weighting
  Geometric shading is converted to a *beam-irradiance-weighted* fraction using a
  clear-sky DNI model (Meinel with Kasten-Young air mass) times cos(AOI). This is a
  geometric shading factor on the beam component only. It is NOT the electrical
  mismatch loss and does not reduce diffuse light; both are reported separately by
  the energy module. With a TMY file the weighting can use measured DNI instead.
"""
from __future__ import annotations
import math
from collections import defaultdict
import numpy as np
from . import geometry as g
from .solar import daylight_samples, sun_vector


def clear_sky_dni(zenith_deg: float) -> float:
    if zenith_deg >= 90:
        return 0.0
    am = 1.0 / (math.cos(math.radians(zenith_deg)) + 0.50572 * (96.07995 - zenith_deg) ** -1.6364)
    return 1353.0 * 0.7 ** (am ** 0.678)


def build_walls(roof, obstacles, parapet_height_m: float):
    """obstacles: iterable of (polygon, height_m, transmittance)."""
    segs, heights, trans = [], [], []
    for poly, h, t in obstacles:
        if h <= 0:
            continue
        for a, b in g.edges(g.clean(poly)):
            segs.append((a, b)); heights.append(h); trans.append(t)
    if parapet_height_m > 0:
        for a, b in g.edges(g.clean(roof)):
            segs.append((a, b)); heights.append(parapet_height_m); trans.append(0.0)
    return (np.array(segs, dtype=float).reshape(-1, 2, 2), np.array(heights, dtype=float),
            np.array(trans, dtype=float))


def _module_normal(c):
    p0, p1, p3 = np.array(c[0]), np.array(c[1]), np.array(c[3])
    n = np.cross(p1 - p0, p3 - p0)
    if n[2] < 0:
        n = -n
    return n / np.linalg.norm(n)


def sample_points(panels, nu: int = 3, nv: int = 3):
    pts, owner = [], []
    for idx, p in enumerate(panels):
        c = np.array(p["corners3d"], dtype=float)
        nrm = _module_normal(p["corners3d"])
        for i in range(nu):
            for j in range(nv):
                a, b = (i + 0.5) / nu, (j + 0.5) / nv
                top = c[0] + (c[1] - c[0]) * a
                bot = c[3] + (c[2] - c[3]) * a
                pts.append(top + (bot - top) * b + nrm * 1e-3)
                owner.append(idx)
    return np.array(pts).reshape(-1, 3), np.array(owner, dtype=int)


def _wall_block(pts, sun, walls, heights, trans):
    """Transmitted fraction (1 = clear) for each point through walls."""
    if len(walls) == 0:
        return np.ones(len(pts))
    hx, hy = sun[0], sun[1]
    hn = math.hypot(hx, hy)
    if hn < 1e-9:
        return np.ones(len(pts))
    dx, dy = hx / hn, hy / hn
    tan_e = sun[2] / hn
    A = walls[:, 0, :]
    E = walls[:, 1, :] - A
    denom = dx * E[:, 1] - dy * E[:, 0]                     # cross(d, e)
    ok = np.abs(denom) > 1e-12
    denom = np.where(ok, denom, 1.0)
    AP = A[None, :, :] - pts[:, None, :2]                    # (Np, Nw, 2)
    t = (AP[..., 0] * E[:, 1] - AP[..., 1] * E[:, 0]) / denom
    u = (AP[..., 0] * dy - AP[..., 1] * dx) / denom
    hit = ok & (t > 1e-6) & (u >= -1e-9) & (u <= 1 + 1e-9)
    z = pts[:, 2:3] + t * tan_e
    blocked = hit & (z < heights[None, :])
    factor = np.where(blocked, trans[None, :], 1.0)
    # Conservative: the most opaque crossed wall governs (opaque => 0). A ray
    # crosses each closed outline twice, so per-wall products would double-count.
    return factor.min(axis=1)


def _cross(a, b):
    return np.stack([a[..., 1] * b[..., 2] - a[..., 2] * b[..., 1],
                     a[..., 2] * b[..., 0] - a[..., 0] * b[..., 2],
                     a[..., 0] * b[..., 1] - a[..., 1] * b[..., 0]], axis=-1)


def _tri_block(pts, owner, sun, tris, tri_owner, chunk=256):
    """True where the ray to the sun hits a module triangle not owned by the point.
    Culling (exact, never discards a possible hit): a triangle can only block a point if
    its highest vertex is above the point and it lies within the ray's horizontal reach."""
    out = np.zeros(len(pts), dtype=bool)
    if len(tris) == 0:
        return out
    d = np.asarray(sun, dtype=float)
    hn = math.hypot(d[0], d[1])
    tan_e = d[2] / hn if hn > 1e-9 else np.inf
    tz_max = tris[:, :, 2].max(axis=1)
    t_lo = tris[:, :, :2].min(axis=1)
    t_hi = tris[:, :, :2].max(axis=1)
    for s in range(0, len(pts), chunk):
        P = pts[s:s + chunk]
        zmin = P[:, 2].min()
        cand = tz_max > zmin + 1e-9
        if not cand.any():
            continue
        reach = (tz_max - zmin) / tan_e if np.isfinite(tan_e) else np.zeros_like(tz_max)
        p_lo, p_hi = P[:, :2].min(axis=0), P[:, :2].max(axis=0)
        ux, uy = (d[0] / hn, d[1] / hn) if hn > 1e-9 else (0.0, 0.0)
        # swept bbox of chunk along the sun direction by each triangle's reach
        ex_lo = np.stack([p_lo[0] + np.minimum(0, ux * reach), p_lo[1] + np.minimum(0, uy * reach)], 1)
        ex_hi = np.stack([p_hi[0] + np.maximum(0, ux * reach), p_hi[1] + np.maximum(0, uy * reach)], 1)
        cand &= (t_hi[:, 0] >= ex_lo[:, 0]) & (t_lo[:, 0] <= ex_hi[:, 0]) & \
                (t_hi[:, 1] >= ex_lo[:, 1]) & (t_lo[:, 1] <= ex_hi[:, 1])
        idx = np.nonzero(cand)[0]
        if len(idx) == 0:
            continue
        T = tris[idx]
        v0, e1, e2 = T[:, 0], T[:, 1] - T[:, 0], T[:, 2] - T[:, 0]
        pv = _cross(np.broadcast_to(d, e2.shape), e2)
        det = (e1 * pv).sum(-1)
        valid = np.abs(det) > 1e-12
        inv = np.where(valid, 1.0 / np.where(valid, det, 1.0), 0.0)
        tv = P[:, None, :] - v0[None, :, :]
        uu = (tv * pv[None]).sum(-1) * inv
        qv = _cross(tv, np.broadcast_to(e1[None], tv.shape))
        vv = (qv @ d) * inv
        tt = (qv * e2[None]).sum(-1) * inv
        hit = valid & (uu >= 0) & (vv >= 0) & (uu + vv <= 1) & (tt > 1e-6)
        hit &= tri_owner[idx][None, :] != owner[s:s + chunk, None]
        out[s:s + chunk] = hit.any(axis=1)
    return out


def analyse(panels, roof, obstacles, latitude, longitude, parapet_height_m=0.0,
            step_min=60, nu=3, nv=3, include_row_shading=True, snapshot_limit=400):
    """obstacles: list of dicts {polygon, height_m, kind, transmittance}."""
    if not panels:
        return {"status": "no_modules", "panel_shading_pct": [], "beam_weighted_shading_pct": 0.0}
    walls, heights, trans = build_walls(
        roof, [(o["polygon"], o.get("height_m", 0), o.get("transmittance", 0.0)) for o in obstacles],
        parapet_height_m)
    pts, owner = sample_points(panels, nu, nv)
    tris, tri_owner = [], []
    if include_row_shading:
        for i, p in enumerate(panels):
            c = p["corners3d"]
            tris += [(c[0], c[1], c[2]), (c[0], c[2], c[3])]
            tri_owner += [i, i]
    tris = np.array(tris, dtype=float).reshape(-1, 3, 3)
    tri_owner = np.array(tri_owner, dtype=int)
    normals = np.array([_module_normal(p["corners3d"]) for p in panels])
    npan = len(panels)
    per_panel_w = np.zeros(npan); per_panel_tot = np.zeros(npan)
    monthly = defaultdict(lambda: [0.0, 0.0])
    timeline = []
    samples = daylight_samples(latitude, longitude, step_min=step_min)
    for t, sp in samples:
        sun = sun_vector(sp.elevation, sp.azimuth)
        cos_aoi = np.clip(normals @ np.array(sun), 0, None)
        beam = clear_sky_dni(sp.zenith) * cos_aoi             # W/m2 per panel
        if beam.sum() <= 0:
            continue
        lit = _wall_block(pts, sun, walls, heights, trans)
        lit = np.where(_tri_block(pts, owner, sun, tris, tri_owner), 0.0, lit)
        shaded_frac = 1.0 - np.bincount(owner, weights=lit, minlength=npan) / (nu * nv)
        per_panel_w += shaded_frac * beam
        per_panel_tot += beam
        m = monthly[t.month]
        m[0] += float((shaded_frac * beam).sum()); m[1] += float(beam.sum())
        if len(timeline) < snapshot_limit:
            timeline.append({"utc": t.isoformat(), "elevation_deg": round(sp.elevation, 2),
                             "azimuth_deg": round(sp.azimuth, 2),
                             "panels_any_shade": int((shaded_frac > 0).sum()),
                             "mean_shaded_area_pct": round(100 * float(shaded_frac.mean()), 2)})
    panel_pct = np.where(per_panel_tot > 0, 100 * per_panel_w / np.maximum(per_panel_tot, 1e-9), 0)
    total = 100 * per_panel_w.sum() / max(per_panel_tot.sum(), 1e-9)
    return {
        "status": "ok",
        "method": "3-D ray casting; clear-sky beam-weighted; representative 15th day of each month",
        "time_step_min": step_min,
        "samples_per_module": nu * nv,
        "beam_weighted_shading_pct": round(float(total), 3),
        "panel_shading_pct": [round(float(x), 3) for x in panel_pct],
        "monthly_beam_shading_pct": {str(k): round(100 * v[0] / v[1], 3) if v[1] else 0.0
                                     for k, v in sorted(monthly.items())},
        "timeline": timeline,
        "limitations": [
            "Geometric beam shading only; diffuse (sky-view) shading is not modelled.",
            "Weighting uses a clear-sky DNI model, not measured weather.",
            "Electrical mismatch/bypass-diode effects are bounded separately, not modelled cell-by-cell.",
            "Obstacle and tree heights are user inputs; accuracy depends on survey data.",
        ],
    }


def shadow_polygons(obstacles, sun_elevation, sun_azimuth):
    """Ground (roof-plane) shadow outline of each prism at one instant (for display)."""
    if sun_elevation <= 0.5:
        return []
    sx, sy, sz = sun_vector(sun_elevation, sun_azimuth)
    k = math.hypot(sx, sy) / sz
    ux, uy = -sx / math.hypot(sx, sy), -sy / math.hypot(sx, sy)
    out = []
    for o in obstacles:
        poly = g.clean(o["polygon"])
        L = o.get("height_m", 0) * k
        if L <= 0:
            continue
        pts = poly + [(x + ux * L, y + uy * L) for x, y in poly]
        out.append({"name": o.get("name", ""), "polygon": convex_hull(pts)})
    return out


def convex_hull(points):
    pts = sorted(set((round(x, 9), round(y, 9)) for x, y in points))
    if len(pts) <= 2:
        return pts
    def cross(o, a, b):
        return (a[0] - o[0]) * (b[1] - o[1]) - (a[1] - o[1]) * (b[0] - o[0])
    lower, upper = [], []
    for p in pts:
        while len(lower) >= 2 and cross(lower[-2], lower[-1], p) <= 0:
            lower.pop()
        lower.append(p)
    for p in reversed(pts):
        while len(upper) >= 2 and cross(upper[-2], upper[-1], p) <= 0:
            upper.pop()
        upper.append(p)
    return lower[:-1] + upper[:-1]
