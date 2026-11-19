"""AI-assisted roof & obstacle detection (Stage 4) using Gemini vision.

Flow: map view (centre, zoom, size) -> server composes the view from the configured tile provider ->
Gemini returns polygons in normalised 0..1000 image coordinates (structured JSON output) -> converted to
WGS84 via Web-Mercator -> validated -> returned as UNVERIFIED GeoJSON suggestions with confidence.
Heights are never inferred from a single overhead image (height_m = null: measure on site).
Use only imagery whose licence permits automated analysis."""
from __future__ import annotations
import base64, io, math, urllib.request
from backend.engine import geometry as g
from backend.engine.geo import LocalFrame

TILE = 256
KINDS = {"roof": "roof", "water_tank": "obstacle", "staircase_headroom": "obstacle", "lift_room": "obstacle",
         "hvac_unit": "obstacle", "parapet": "obstacle", "skylight": "keepout", "existing_solar": "keepout",
         "chimney": "obstacle", "ventilation": "obstacle", "tree": "tree", "nearby_building": "building",
         "other_obstruction": "obstacle"}
SCHEMA = {"type": "OBJECT", "properties": {"features": {"type": "ARRAY", "items": {"type": "OBJECT", "properties": {
    "kind": {"type": "STRING", "enum": sorted(KINDS)}, "label": {"type": "STRING"},
    "confidence": {"type": "NUMBER"},
    "polygon": {"type": "ARRAY", "items": {"type": "ARRAY", "items": {"type": "INTEGER"}}}},
    "required": ["kind", "confidence", "polygon"]}}}, "required": ["features"]}
PROMPT = ("Overhead image of a building site, north up. Identify rooftop outlines and rooftop obstructions "
          "(water tanks, staircase headrooms, lift rooms, HVAC units, parapets, skylights, existing solar, chimneys, "
          "ventilation), trees and nearby buildings. Return polygons as [x, y] points normalised 0-1000 "
          "(x left to right, y top to bottom), 3-30 points each, tracing visible edges. Give an honest confidence 0-1; "
          "omit anything you are unsure is present. Do not estimate heights.")


def lonlat_to_px(lon, lat, z):
    s = TILE * 2 ** z
    sin = math.sin(max(-85.0511, min(85.0511, lat)) * math.pi / 180)
    return (lon + 180) / 360 * s, (0.5 - math.log((1 + sin) / (1 - sin)) / (4 * math.pi)) * s


def px_to_lonlat(x, y, z):
    s = TILE * 2 ** z
    n = math.pi - 2 * math.pi * y / s
    return x / s * 360 - 180, 180 / math.pi * math.atan(math.sinh(n))


def compose_view(tile_url: str, lat: float, lon: float, zoom: int, width: int, height: int, opener=None) -> bytes:
    """Stitch provider tiles into one PNG exactly matching the map view (same maths as the browser map)."""
    from PIL import Image
    if not (64 <= width <= 1600 and 64 <= height <= 1600 and 12 <= zoom <= 22):
        raise ValueError("View must be 64-1600 px and zoom 12-22")
    op = opener or (lambda u: urllib.request.urlopen(urllib.request.Request(u, headers={"User-Agent": "SUNITE/0.8"}), timeout=20))
    cx, cy = lonlat_to_px(lon, lat, zoom)
    x0, y0 = cx - width / 2, cy - height / 2
    img = Image.new("RGB", (width, height), (200, 200, 200))
    for tx in range(int(x0 // TILE), int((x0 + width) // TILE) + 1):
        for ty in range(int(y0 // TILE), int((y0 + height) // TILE) + 1):
            if not 0 <= ty < 2 ** zoom:
                continue
            with op(tile_url.replace("{z}", str(zoom)).replace("{x}", str(tx % 2 ** zoom)).replace("{y}", str(ty))) as r:
                tile = Image.open(io.BytesIO(r.read())).convert("RGB")
            img.paste(tile, (int(round(tx * TILE - x0)), int(round(ty * TILE - y0))))
    buf = io.BytesIO(); img.save(buf, "PNG"); return buf.getvalue()


def to_features(result: dict, view: dict, min_confidence=0.3) -> dict:
    z, w, h = view["zoom"], view["width_px"], view["height_px"]
    cx, cy = lonlat_to_px(view["center_lon"], view["center_lat"], z)
    fr = LocalFrame(view["center_lat"], view["center_lon"])
    feats, rejected = [], []
    for i, f in enumerate((result or {}).get("features", [])[:200]):
        kind = f.get("kind")
        conf = f.get("confidence")
        pts = f.get("polygon") or []
        why = None
        if kind not in KINDS:
            why = f"unknown kind {kind}"
        elif not isinstance(conf, (int, float)) or not 0 <= conf <= 1:
            why = "invalid confidence"
        elif conf < min_confidence:
            why = f"confidence {conf} below {min_confidence}"
        elif len(pts) < 3 or any(len(p) != 2 or not all(isinstance(v, (int, float)) and 0 <= v <= 1000 for v in p) for p in pts):
            why = "invalid polygon coordinates"
        if why:
            rejected.append({"index": i, "reason": why}); continue
        ring = [px_to_lonlat(cx - w / 2 + p[0] / 1000 * w, cy - h / 2 + p[1] / 1000 * h, z) for p in pts]
        local = g.clean([fr.to_local(la, lo) for lo, la in ring])
        if len(local) < 3 or not g.is_simple(local):
            rejected.append({"index": i, "reason": "self-intersecting or degenerate outline"}); continue
        coords = [[round(lo, 8), round(la, 8)] for lo, la in ring]
        coords.append(coords[0])
        feats.append({"type": "Feature", "geometry": {"type": "Polygon", "coordinates": [coords]},
                      "properties": {"role": KINDS[kind], "name": f.get("label") or kind.replace("_", " "), "detected_kind": kind,
                                     "confidence": round(float(conf), 2), "height_m": None, "ai_detected": True,
                                     "status": "unverified", "area_m2": round(g.area(local), 1)}})
    return {"type": "FeatureCollection", "features": feats, "rejected": rejected,
            "notice": "AI suggestions are unverified. Check every outline against the imagery and site survey; "
                      "enter obstacle heights from measurements before running shading."}


def detect(provider, image_png: bytes, view: dict, min_confidence=0.3) -> dict:
    if len(image_png) > 8_000_000:
        raise ValueError("Image larger than 8 MB")
    out = provider.complete("You are a precise remote-sensing assistant for rooftop solar surveys.",
                            [{"role": "user", "content": PROMPT,
                              "images": [{"mime": "image/png", "b64": base64.b64encode(image_png).decode()}]}],
                            None, response_schema=SCHEMA, temperature=0.0)
    import json
    try:
        parsed = json.loads(out.get("text") or "{}")
    except json.JSONDecodeError:
        return {"type": "FeatureCollection", "features": [], "rejected": [{"reason": "model did not return valid JSON"}],
                "notice": "Detection failed; trace manually."}
    res = to_features(parsed, view, min_confidence)
    res["usage"] = out.get("usage", {})
    return res
