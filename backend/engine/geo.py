"""Geo-referencing (Stage 3 backend): WGS84 <-> local metres, UTM, location parsing, GeoJSON & DXF import/export.

Local frame: exact ECEF -> East-North-Up tangent plane at a site origin, so +x = east, +y = true north
(the engine's convention). Round-trip error is sub-millimetre for rooftop and plant-scale extents.
UTM uses the Krueger series (accurate to ~1 mm within a zone) for CAD geo-referencing."""
from __future__ import annotations
import math, re
from . import geometry as g

A_WGS = 6378137.0
F_WGS = 1 / 298.257223563
E2 = F_WGS * (2 - F_WGS)


# ---------------------------------------------------------------- local ENU frame
def _ecef(lat, lon, h=0.0):
    p, l = math.radians(lat), math.radians(lon)
    n = A_WGS / math.sqrt(1 - E2 * math.sin(p) ** 2)
    return ((n + h) * math.cos(p) * math.cos(l), (n + h) * math.cos(p) * math.sin(l), (n * (1 - E2) + h) * math.sin(p))


def _geodetic(x, y, z):
    lon = math.atan2(y, x)
    p = math.hypot(x, y)
    lat = math.atan2(z, p * (1 - E2))
    for _ in range(8):
        n = A_WGS / math.sqrt(1 - E2 * math.sin(lat) ** 2)
        h = p / math.cos(lat) - n
        lat = math.atan2(z, p * (1 - E2 * n / (n + h)))
    n = A_WGS / math.sqrt(1 - E2 * math.sin(lat) ** 2)
    return math.degrees(lat), math.degrees(lon), p / math.cos(lat) - n


class LocalFrame:
    def __init__(self, lat0: float, lon0: float):
        if not (-90 <= lat0 <= 90 and -180 <= lon0 <= 180):
            raise ValueError("Origin latitude/longitude out of range")
        self.lat0, self.lon0 = lat0, lon0
        self.o = _ecef(lat0, lon0)
        p, l = math.radians(lat0), math.radians(lon0)
        self.e = (-math.sin(l), math.cos(l), 0.0)
        self.n = (-math.sin(p) * math.cos(l), -math.sin(p) * math.sin(l), math.cos(p))
        self.u = (math.cos(p) * math.cos(l), math.cos(p) * math.sin(l), math.sin(p))

    def to_local(self, lat, lon):
        x, y, z = _ecef(lat, lon)
        d = (x - self.o[0], y - self.o[1], z - self.o[2])
        return (sum(a * b for a, b in zip(d, self.e)), sum(a * b for a, b in zip(d, self.n)))

    def to_geo(self, x, y):
        # point on the tangent plane, then project down onto the ellipsoid surface
        X = [self.o[i] + x * self.e[i] + y * self.n[i] for i in range(3)]
        lat, lon, _ = _geodetic(*X)
        for _ in range(3):  # refine so that to_local(to_geo(p)) == p
            ex, ey = self.to_local(lat, lon)
            X = [X[i] + (x - ex) * self.e[i] + (y - ey) * self.n[i] for i in range(3)]
            lat, lon, _ = _geodetic(*X)
        return lat, lon


# ---------------------------------------------------------------- UTM (Krueger)
_n = F_WGS / (2 - F_WGS)
_A = A_WGS / (1 + _n) * (1 + _n ** 2 / 4 + _n ** 4 / 64)
_al = (_n / 2 - 2 * _n ** 2 / 3 + 5 * _n ** 3 / 16, 13 * _n ** 2 / 48 - 3 * _n ** 3 / 5, 61 * _n ** 3 / 240)
_be = (_n / 2 - 2 * _n ** 2 / 3 + 37 * _n ** 3 / 96, _n ** 2 / 48 + _n ** 3 / 15, 17 * _n ** 3 / 480)
_de = (2 * _n - 2 * _n ** 2 / 3 - 2 * _n ** 3, 7 * _n ** 2 / 3 - 8 * _n ** 3 / 5, 56 * _n ** 3 / 15)
K0, E0 = 0.9996, 500000.0


def utm_zone(lat, lon):
    if 56 <= lat < 64 and 3 <= lon < 12:
        return 32
    if 72 <= lat < 84 and 0 <= lon < 42:
        return 31 if lon < 9 else 33 if lon < 21 else 35 if lon < 33 else 37
    return int((lon + 180) // 6) % 60 + 1


def to_utm(lat, lon, zone=None):
    zone = zone or utm_zone(lat, lon)
    lam0 = math.radians(zone * 6 - 183)
    phi, lam = math.radians(lat), math.radians(lon) - lam0
    c = 2 * math.sqrt(_n) / (1 + _n)
    t = math.sinh(math.atanh(math.sin(phi)) - c * math.atanh(c * math.sin(phi)))
    xi, eta = math.atan2(t, math.cos(lam)), math.atanh(math.sin(lam) / math.sqrt(1 + t * t))
    E = E0 + K0 * _A * (eta + sum(a * math.cos(2 * j * xi) * math.sinh(2 * j * eta) for j, a in enumerate(_al, 1)))
    N = K0 * _A * (xi + sum(a * math.sin(2 * j * xi) * math.cosh(2 * j * eta) for j, a in enumerate(_al, 1)))
    if lat < 0:
        N += 10_000_000
    return {"zone": zone, "hemisphere": "N" if lat >= 0 else "S", "easting": E, "northing": N,
            "epsg": (32600 if lat >= 0 else 32700) + zone}


def from_utm(easting, northing, zone, hemisphere="N"):
    N = northing - (10_000_000 if hemisphere == "S" else 0)
    xi, eta = N / (K0 * _A), (easting - E0) / (K0 * _A)
    xp = xi - sum(b * math.sin(2 * j * xi) * math.cosh(2 * j * eta) for j, b in enumerate(_be, 1))
    ep = eta - sum(b * math.cos(2 * j * xi) * math.sinh(2 * j * eta) for j, b in enumerate(_be, 1))
    chi = math.asin(math.sin(xp) / math.cosh(ep))
    phi = chi + sum(d * math.sin(2 * j * chi) for j, d in enumerate(_de, 1))
    lam = math.radians(zone * 6 - 183) + math.atan2(math.sinh(ep), math.cos(xp))
    return math.degrees(phi), math.degrees(lam)


# ---------------------------------------------------------------- location input parsing
_DMS = re.compile(r"(\d{1,3})\s*[°d]\s*(\d{1,2})\s*['m]\s*([\d.]+)\s*(?:\"|''|s)?\s*([NSEW])", re.I)


def parse_location(text: str) -> dict:
    """Accepts 'lat, lon', DMS, or a Google Maps URL. Returns {'status','lat','lon','source'}."""
    s = (text or "").strip()
    if not s:
        return {"status": "error", "message": "Empty location"}
    if re.search(r"(goo\.gl|maps\.app\.goo\.gl)/", s):
        return {"status": "needs_resolution", "message": "Short Google link: the server must follow the redirect "
                "(internet required), or paste the full URL / coordinates.", "url": s}
    for pat, src in [(r"!3d(-?\d+\.\d+)!4d(-?\d+\.\d+)", "google_place_pin"),
                     (r"[?&](?:q|query|ll|center|destination)=(-?\d+\.\d+)(?:,|%2C)\s*(-?\d+\.\d+)", "google_query"),
                     (r"@(-?\d+\.\d+),(-?\d+\.\d+)", "google_viewport_centre")]:
        m = re.search(pat, s)
        if m:
            return _ok(float(m.group(1)), float(m.group(2)), src)
    dms = _DMS.findall(s)
    if len(dms) == 2:
        vals = {}
        for d, mi, se, hemi in dms:
            v = int(d) + int(mi) / 60 + float(se) / 3600
            h = hemi.upper()
            vals["lat" if h in "NS" else "lon"] = -v if h in "SW" else v
        if "lat" in vals and "lon" in vals:
            return _ok(vals["lat"], vals["lon"], "dms")
    m = re.fullmatch(r"\s*(-?\d+(?:\.\d+)?)\s*[, ]\s*(-?\d+(?:\.\d+)?)\s*", s)
    if m:
        return _ok(float(m.group(1)), float(m.group(2)), "decimal")
    return {"status": "error", "message": "Could not read a location; paste 'lat, lon' or a full Google Maps link"}


def _ok(lat, lon, src):
    if not (-90 <= lat <= 90 and -180 <= lon <= 180):
        return {"status": "error", "message": f"Coordinates out of range ({lat}, {lon})"}
    note = "Viewport centre of the map, not necessarily the building; confirm the pin." if src == "google_viewport_centre" else None
    return {"status": "ok", "lat": round(lat, 7), "lon": round(lon, 7), "source": src, **({"note": note} if note else {})}


# ---------------------------------------------------------------- GeoJSON
ROLES = {"roof", "obstacle", "keepout", "tree", "building"}


def _ring(coords, where):
    if len(coords) < 4:
        raise ValueError(f"{where}: polygon ring needs at least 4 positions (closed)")
    for c in coords:
        if len(c) < 2 or not (-180 <= c[0] <= 180 and -90 <= c[1] <= 90):
            raise ValueError(f"{where}: invalid position {c} (GeoJSON order is [lon, lat])")
    if coords[0][:2] != coords[-1][:2]:
        raise ValueError(f"{where}: ring is not closed")
    return [(c[1], c[0]) for c in coords[:-1]]   # -> (lat, lon)


def import_geojson(fc: dict, origin: tuple | None = None) -> dict:
    if fc.get("type") != "FeatureCollection":
        raise ValueError("Expected a GeoJSON FeatureCollection")
    feats = fc.get("features") or []
    if not feats or len(feats) > 2000:
        raise ValueError("FeatureCollection must contain 1-2000 features")
    raw, warnings = [], []
    for i, f in enumerate(feats):
        geom, props = f.get("geometry") or {}, f.get("properties") or {}
        role = str(props.get("role", "roof")).lower()
        if role not in ROLES:
            raise ValueError(f"feature {i}: role must be one of {sorted(ROLES)}")
        polys = [geom["coordinates"]] if geom.get("type") == "Polygon" else \
            geom["coordinates"] if geom.get("type") == "MultiPolygon" else None
        if polys is None:
            warnings.append(f"feature {i}: {geom.get('type')} ignored (only Polygon/MultiPolygon)")
            continue
        for k, rings in enumerate(polys):
            name = props.get("name") or f"{role}-{i + 1}" + (f"-{k + 1}" if len(polys) > 1 else "")
            raw.append({"role": role, "name": name, "outer": _ring(rings[0], f"feature {i}"),
                        "holes": [_ring(h, f"feature {i} hole") for h in rings[1:]], "props": props})
    roofs_raw = [r for r in raw if r["role"] == "roof"]
    if not roofs_raw:
        raise ValueError("No feature with role 'roof'")
    if origin is None:
        pts = [p for r in roofs_raw for p in r["outer"]]
        origin = (sum(p[0] for p in pts) / len(pts), sum(p[1] for p in pts) / len(pts))
    fr = LocalFrame(*origin)
    loc = lambda ring: [tuple(round(v, 4) for v in fr.to_local(la, lo)) for la, lo in ring]
    roofs = []
    for r in roofs_raw:
        poly = loc(r["outer"])
        if not g.is_simple(poly):
            raise ValueError(f"Roof '{r['name']}' is self-intersecting")
        roofs.append({"name": r["name"], "polygon": poly, "elevation_m": _num(r["props"].get("elevation_m")),
                      "parapet_height_m": _num(r["props"].get("parapet_height_m")) or 0.0,
                      "obstacles": [{"name": f"{r['name']} void {j + 1}", "polygon": loc(h), "height_m": 0.0,
                                     "kind": "keepout"} for j, h in enumerate(r["holes"])]})
    context = []
    for r in raw:
        if r["role"] == "roof":
            continue
        poly = loc(r["outer"])
        h = _num(r["props"].get("height_m"))
        if h is None and r["role"] != "keepout":
            warnings.append(f"'{r['name']}': no height_m; set it from survey before shading results are relied on")
        ob = {"name": r["name"], "polygon": poly, "height_m": h or 0.0,
              "kind": "obstacle" if r["role"] == "obstacle" else r["role"],
              "transmittance": _num(r["props"].get("transmittance")) or 0.0}
        c = g.centroid(poly)
        host = next((rf for rf in roofs if g.point_in_polygon(c, rf["polygon"])), None)
        if host:
            host["obstacles"].append(ob)
        else:
            context.append(ob)       # adjacent buildings / trees: shading only
    if len(context):
        for rf in roofs:
            rf["obstacles"] += [dict(o, kind=o["kind"] if o["kind"] in ("tree", "building") else "building") for o in context]
    return {"origin": {"lat": origin[0], "lon": origin[1]}, "roofs": roofs, "warnings": warnings,
            "utm": to_utm(*origin)}


def _num(v):
    try:
        return None if v in (None, "") else float(v)
    except (TypeError, ValueError):
        return None


def export_geojson(origin: dict, layers: dict) -> dict:
    """layers: {role: [{'name','polygon'(local m), ...props}]} -> FeatureCollection in WGS84."""
    fr = LocalFrame(origin["lat"], origin["lon"])
    feats = []
    for role, items in layers.items():
        for it in items:
            ring = [list(map(lambda v: round(v, 8), fr.to_geo(*p)[::-1])) for p in it["polygon"]]
            ring.append(ring[0])
            props = {k: v for k, v in it.items() if k != "polygon"}
            feats.append({"type": "Feature", "properties": {"role": role, **props},
                          "geometry": {"type": "Polygon", "coordinates": [ring]}})
    return {"type": "FeatureCollection", "features": feats}


# ---------------------------------------------------------------- DXF (ASCII) import
_UNITS = {0: None, 1: 0.0254, 2: 0.3048, 4: 0.001, 5: 0.01, 6: 1.0}


def import_dxf(text: str, units_override_m: float | None = None) -> dict:
    """Closed LWPOLYLINE / POLYLINE entities. Layer names decide role: *ROOF* -> roof, *KEEP*/*WALK*/*FIRE*
    -> keep-out, *TREE*, *BUILD*, anything else -> obstacle. A layer suffix like _H2.5 sets height in metres."""
    lines = text.replace("\r\n", "\n").split("\n")
    if len(lines) > 2_000_000:
        raise ValueError("DXF too large")
    pairs = [(lines[i].strip(), lines[i + 1].rstrip()) for i in range(0, len(lines) - 1, 2)]
    scale, warnings = None, []
    for i, (c, v) in enumerate(pairs):
        if c == "9" and v == "$INSUNITS" and i + 1 < len(pairs):
            scale = _UNITS.get(int(pairs[i + 1][1]))
    if units_override_m:
        scale = units_override_m
    if scale is None:
        scale = 1.0
        warnings.append("DXF has no $INSUNITS; assumed metres. Confirm the drawing scale.")
    polys, cur, in_ent = [], None, False
    for c, v in pairs:
        if c == "2" and v == "ENTITIES":
            in_ent = True
        if not in_ent:
            continue
        if c == "0":
            if cur and cur["type"] == "LWPOLYLINE":
                polys.append(cur); cur = None
            if v == "LWPOLYLINE":
                cur = {"type": "LWPOLYLINE", "layer": "0", "closed": False, "pts": [], "_x": None}
            elif v == "POLYLINE":
                cur = {"type": "POLYLINE", "layer": "0", "closed": False, "pts": [], "_x": None}
            elif v == "VERTEX" and cur and cur["type"] == "POLYLINE":
                cur["_in_vertex"] = True
            elif v == "SEQEND" and cur and cur["type"] == "POLYLINE":
                polys.append(cur); cur = None
            elif v == "ENDSEC":
                break
            continue
        if not cur:
            continue
        if c == "8" and not cur.get("_in_vertex"):
            cur["layer"] = v
        elif c == "70" and not cur.get("_in_vertex"):
            cur["closed"] = bool(int(v) & 1)
        elif c == "10":
            cur["_x"] = float(v)
        elif c == "20" and cur["_x"] is not None:
            cur["pts"].append((cur["_x"] * scale, float(v) * scale)); cur["_x"] = None
    roofs, obstacles = [], []
    for p in polys:
        pts = g.clean(p["pts"])
        if not p["closed"] and not (len(p["pts"]) > 3 and math.dist(p["pts"][0], p["pts"][-1]) < 1e-6):
            warnings.append(f"Open polyline on layer {p['layer']} ignored")
            continue
        if len(pts) < 3 or not g.is_simple(pts):
            warnings.append(f"Invalid polygon on layer {p['layer']} ignored")
            continue
        L = p["layer"].upper()
        m = re.search(r"_H(\d+(?:\.\d+)?)", L)
        h = float(m.group(1)) if m else None
        item = {"name": f"{p['layer']}-{len(roofs) + len(obstacles) + 1}", "polygon": [tuple(round(v, 4) for v in q) for q in pts]}
        if "ROOF" in L:
            roofs.append({**item, "obstacles": [], "parapet_height_m": 0.0})
        else:
            kind = "keepout" if any(k in L for k in ("KEEP", "WALK", "FIRE")) else "tree" if "TREE" in L else \
                "building" if "BUILD" in L else "obstacle"
            if h is None and kind != "keepout":
                warnings.append(f"{item['name']}: no _H<metres> layer suffix; height must be entered")
            obstacles.append({**item, "height_m": h or 0.0, "kind": kind})
    if not roofs:
        raise ValueError("No closed polyline on a layer containing 'ROOF'")
    for o in obstacles:
        c = g.centroid(o["polygon"])
        host = next((r for r in roofs if g.point_in_polygon(c, r["polygon"])), None)
        for r in ([host] if host else roofs):
            r["obstacles"].append(o)
    return {"roofs": roofs, "units_scale_to_m": scale, "warnings": warnings,
            "note": "DXF coordinates are local; set north_rotation_deg and the site origin before simulation."}
