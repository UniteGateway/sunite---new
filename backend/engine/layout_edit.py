"""Manual layout editing (Stage 5): validate a user-edited module set against the same rules as the
automatic placer (inside roof with setback, obstacle clearance, no overlap), and compare scenarios."""
from __future__ import annotations
from . import geometry as g


def validate_layout(roof, obstacles, footprints: dict, *, setback_m: float, clearance_m: float, locked: set | None = None):
    """footprints: {module_id: [[x,y]x4]}. Returns per-module issues; locked modules are reported, never moved."""
    roof = g.clean(roof)
    obs = [(o.get("name", "Obstacle"), g.clean(o["polygon"]), o.get("clearance_m", clearance_m)) for o in obstacles]
    ids, issues = list(footprints), {}
    rects = {i: [tuple(p) for p in footprints[i]] for i in ids}
    for i in ids:
        r, why = rects[i], []
        if len(r) != 4 or g.area(r) < 0.1:
            why.append("not a valid module rectangle")
        elif not g.rect_within(r, roof, setback_m):
            why.append(f"outside roof or within {setback_m} m setback")
        for name, poly, clr in obs:
            if g.polygon_distance(r, poly) < clr - 1e-9 or (clr <= 0 and g.polygons_intersect(r, poly)):
                why.append(f"closer than {clr} m to {name}")
        if why:
            issues[i] = why
    cents = {i: g.centroid(rects[i]) for i in ids}
    for a in range(len(ids)):
        for b in range(a + 1, len(ids)):
            i, k = ids[a], ids[b]
            if abs(cents[i][0] - cents[k][0]) > 3 or abs(cents[i][1] - cents[k][1]) > 3:
                continue
            ri, rk = rects[i], rects[k]
            overlap = g.point_in_polygon(cents[i], rk) or g.point_in_polygon(cents[k], ri) or any(
                g.segments_cross(p1, p2, q1, q2) for p1, p2 in g.edges(ri) for q1, q2 in g.edges(rk))
            if overlap:
                issues.setdefault(i, []).append(f"overlaps {k}")
                issues.setdefault(k, []).append(f"overlaps {i}")
    return {"valid": not issues, "module_count": len(ids), "invalid": issues,
            "locked_with_issues": sorted(set(issues) & (locked or set()))}


def move_module(fp, dx, dy):
    return [[p[0] + dx, p[1] + dy] for p in fp]


def compare_scenarios(scenarios: dict) -> list[dict]:
    """scenarios: {name: design result}. Side-by-side KPIs for decision making."""
    rows = []
    for name, r in scenarios.items():
        e = r.get("energy") or {}
        rows.append({"scenario": name, "modules": r["panel_count"], "dc_kwp": r["dc_kwp"],
                     "shading_pct": r["shading"].get("beam_weighted_shading_pct"),
                     "annual_kwh": e.get("annual_ac_kwh"), "specific_yield": e.get("specific_yield_kwh_per_kwp"),
                     "grand_total_priced": r["boq_totals"]["grand_total_priced_items"],
                     "boq_complete": r["boq_totals"]["complete"]})
    best = max(rows, key=lambda x: x["dc_kwp"]) if rows else None
    for x in rows:
        x["most_capacity"] = x is best
    return rows
