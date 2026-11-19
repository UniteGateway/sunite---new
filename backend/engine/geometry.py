"""Pure-Python planar geometry used by the engineering engine.

Coordinates are metres in a local "true" frame: +x = east, +y = north.
No third-party dependency, so every routine is unit-testable in isolation.
"""
from __future__ import annotations
import math
from typing import Iterable, Sequence

Pt = tuple[float, float]
EPS = 1e-9


def signed_area(poly: Sequence[Pt]) -> float:
    a = 0.0
    n = len(poly)
    for i in range(n):
        x1, y1 = poly[i]
        x2, y2 = poly[(i + 1) % n]
        a += x1 * y2 - x2 * y1
    return a / 2.0


def area(poly: Sequence[Pt]) -> float:
    return abs(signed_area(poly))


def centroid(poly: Sequence[Pt]) -> Pt:
    a = signed_area(poly)
    if abs(a) < EPS:
        xs, ys = zip(*poly)
        return sum(xs) / len(xs), sum(ys) / len(ys)
    cx = cy = 0.0
    n = len(poly)
    for i in range(n):
        x1, y1 = poly[i]
        x2, y2 = poly[(i + 1) % n]
        f = x1 * y2 - x2 * y1
        cx += (x1 + x2) * f
        cy += (y1 + y2) * f
    return cx / (6 * a), cy / (6 * a)


def clean(poly: Iterable[Sequence[float]]) -> list[Pt]:
    """Drop a repeated closing vertex and consecutive duplicates."""
    out: list[Pt] = []
    for p in poly:
        q = (float(p[0]), float(p[1]))
        if not out or math.dist(out[-1], q) > 1e-7:
            out.append(q)
    if len(out) > 1 and math.dist(out[0], out[-1]) <= 1e-7:
        out.pop()
    return out


def _orient(a: Pt, b: Pt, c: Pt) -> float:
    return (b[0] - a[0]) * (c[1] - a[1]) - (b[1] - a[1]) * (c[0] - a[0])


def _on_seg(a: Pt, b: Pt, c: Pt) -> bool:
    return min(a[0], b[0]) - EPS <= c[0] <= max(a[0], b[0]) + EPS and \
        min(a[1], b[1]) - EPS <= c[1] <= max(a[1], b[1]) + EPS


def segments_intersect(p1: Pt, p2: Pt, q1: Pt, q2: Pt) -> bool:
    d1, d2 = _orient(q1, q2, p1), _orient(q1, q2, p2)
    d3, d4 = _orient(p1, p2, q1), _orient(p1, p2, q2)
    if ((d1 > EPS and d2 < -EPS) or (d1 < -EPS and d2 > EPS)) and \
       ((d3 > EPS and d4 < -EPS) or (d3 < -EPS and d4 > EPS)):
        return True
    if abs(d1) <= EPS and _on_seg(q1, q2, p1): return True
    if abs(d2) <= EPS and _on_seg(q1, q2, p2): return True
    if abs(d3) <= EPS and _on_seg(p1, p2, q1): return True
    if abs(d4) <= EPS and _on_seg(p1, p2, q2): return True
    return False


def is_simple(poly: Sequence[Pt]) -> bool:
    """True if polygon has >=3 vertices, non-zero area and no self-intersections."""
    n = len(poly)
    if n < 3 or area(poly) < 1e-6:
        return False
    for i in range(n):
        a1, a2 = poly[i], poly[(i + 1) % n]
        for j in range(i + 1, n):
            if j == i or (j + 1) % n == i or j == (i + 1) % n:
                continue  # adjacent edges share a vertex
            if segments_intersect(a1, a2, poly[j], poly[(j + 1) % n]):
                return False
    return True


def point_in_polygon(p: Pt, poly: Sequence[Pt]) -> bool:
    x, y = p
    inside = False
    n = len(poly)
    j = n - 1
    for i in range(n):
        xi, yi = poly[i]
        xj, yj = poly[j]
        if (yi > y) != (yj > y):
            xint = (xj - xi) * (y - yi) / (yj - yi) + xi
            if x < xint:
                inside = not inside
        j = i
    return inside


def point_segment_distance(p: Pt, a: Pt, b: Pt) -> float:
    ax, ay = a
    dx, dy = b[0] - ax, b[1] - ay
    l2 = dx * dx + dy * dy
    if l2 < EPS:
        return math.dist(p, a)
    t = max(0.0, min(1.0, ((p[0] - ax) * dx + (p[1] - ay) * dy) / l2))
    return math.hypot(p[0] - (ax + t * dx), p[1] - (ay + t * dy))


def edges(poly: Sequence[Pt]):
    n = len(poly)
    for i in range(n):
        yield poly[i], poly[(i + 1) % n]


def polygons_intersect(a: Sequence[Pt], b: Sequence[Pt]) -> bool:
    for e1 in edges(a):
        for e2 in edges(b):
            if segments_intersect(e1[0], e1[1], e2[0], e2[1]):
                return True
    return point_in_polygon(a[0], b) or point_in_polygon(b[0], a)


def polygon_distance(a: Sequence[Pt], b: Sequence[Pt]) -> float:
    """Minimum distance between two simple polygons; 0 when they touch/overlap."""
    if polygons_intersect(a, b):
        return 0.0
    d = math.inf
    for p in a:
        for s, e in edges(b):
            d = min(d, point_segment_distance(p, s, e))
    for p in b:
        for s, e in edges(a):
            d = min(d, point_segment_distance(p, s, e))
    return d


def segments_cross(p1: Pt, p2: Pt, q1: Pt, q2: Pt) -> bool:
    """Proper crossing only (touching / collinear contact is not a crossing)."""
    d1, d2 = _orient(q1, q2, p1), _orient(q1, q2, p2)
    d3, d4 = _orient(p1, p2, q1), _orient(p1, p2, q2)
    return ((d1 > EPS and d2 < -EPS) or (d1 < -EPS and d2 > EPS)) and \
           ((d3 > EPS and d4 < -EPS) or (d3 < -EPS and d4 > EPS))


def point_in_or_on(p: Pt, poly: Sequence[Pt], tol: float = 1e-7) -> bool:
    if point_in_polygon(p, poly):
        return True
    return any(point_segment_distance(p, a, b) <= tol for a, b in edges(poly))


def rect_within(rect: Sequence[Pt], outer: Sequence[Pt], setback: float) -> bool:
    """Rectangle lies inside `outer` (boundary contact allowed when setback == 0)
    with at least `setback` metres to its boundary, including concave corners."""
    if not all(point_in_or_on(p, outer) for p in rect):
        return False
    if not point_in_or_on(centroid(rect), outer):
        return False
    for r in edges(rect):
        for o in edges(outer):
            if segments_cross(r[0], r[1], o[0], o[1]):
                return False
    for p in outer:   # a reflex roof vertex strictly inside the rectangle
        if point_in_polygon(p, rect) and min(point_segment_distance(p, a, b) for a, b in edges(rect)) > 1e-7:
            return False
    if setback <= 0:
        return True
    for p in rect:
        for s, e in edges(outer):
            if point_segment_distance(p, s, e) < setback - 1e-9:
                return False
    for p in outer:  # re-entrant (concave) roof vertices close to the rectangle
        for s, e in edges(rect):
            if point_segment_distance(p, s, e) < setback - 1e-9:
                return False
    return True


def rotate_pt(p: Pt, deg: float, origin: Pt = (0.0, 0.0)) -> Pt:
    r = math.radians(deg)
    c, s = math.cos(r), math.sin(r)
    x, y = p[0] - origin[0], p[1] - origin[1]
    return origin[0] + x * c - y * s, origin[1] + x * s + y * c


def bounds(poly: Sequence[Pt]):
    xs, ys = zip(*poly)
    return min(xs), min(ys), max(xs), max(ys)
