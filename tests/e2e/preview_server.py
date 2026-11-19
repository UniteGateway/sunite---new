"""Flask stand-in exposing the real engine + geo code on the same paths as the FastAPI app, for browser
end-to-end tests in environments where FastAPI is not installed. Not a production server."""
import os, sys
ROOT_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, ROOT_DIR)
from flask import Flask, request, jsonify, send_file, send_from_directory
from backend.engine.pipeline import run_design, legacy_to_request, legacy_response
from backend.engine import geo
from backend.engine.solar import sun_path_day
FE = os.path.join(ROOT_DIR, "frontend")
app = Flask(__name__, static_folder=None)
for page in ("", "map", "viewer", "operations", "equipment"):
    app.add_url_rule("/" + page, page or "index", (lambda f=("index" if not page else page): send_file(f"{FE}/{f}.html")))
@app.get("/static/<path:p>")
def st(p): return send_from_directory(FE, p)
@app.get("/api/me")
def me(): return jsonify({"full_name": "E2E", "role": "company_admin", "organizations": []})
@app.get("/api/projects")
def pr(): return jsonify([])
@app.get("/api/config/map")
def cfg(): return jsonify({"layers": [{"name": "Street map", "url": "http://127.0.0.1:9/{z}/{x}/{y}.png", "attribution": "test tiles"}]})
@app.post("/api/v3/geo/parse-location")
def loc():
    r = geo.parse_location(request.get_json()["text"])
    if r["status"] == "ok": r["utm"] = geo.to_utm(r["lat"], r["lon"])
    return jsonify(r)
@app.post("/api/v3/geo/import-geojson")
def gj():
    b = request.get_json()
    try: return jsonify(geo.import_geojson(b["geojson"]))
    except ValueError as e: return jsonify({"detail": str(e)}), 422
@app.post("/api/design")
def des():
    s = request.get_json(); s.setdefault("obstacles", [])
    try: return jsonify(legacy_response(s, run_design(legacy_to_request(s))))
    except ValueError as e: return jsonify({"detail": str(e)}), 422
@app.post("/api/v3/sun-path")
def sp():
    b = request.get_json(); return jsonify(sun_path_day(b["latitude"], b["longitude"], b["date"], b.get("step_min", 30)))


# --- test fixture: canned AI detection (exercises the real validation + georeferencing code) ---
@app.get("/api/ai/status")
def ai_status(): return jsonify({"configured": True, "model": "test-fixture", "satellite_configured": False})
@app.post("/api/v3/geo/ai-detect")
def ai_detect():
    from backend.ai.vision import to_features
    b = request.get_json()
    view = {k: b[k] for k in ("center_lat", "center_lon", "zoom", "width_px", "height_px")}
    canned = {"features": [{"kind": "roof", "label": "Detected roof", "confidence": 0.9, "polygon": [[380, 380], [620, 380], [620, 620], [380, 620]]},
                           {"kind": "water_tank", "label": "Tank", "confidence": 0.8, "polygon": [[470, 470], [520, 470], [520, 520], [470, 520]]},
                           {"kind": "roof", "confidence": 0.9, "polygon": [[0, 0], [100, 100], [0, 100], [100, 0]]}]}
    return jsonify(to_features(canned, view))


if __name__ == "__main__":
    app.run(port=int(os.environ.get("PORT", 8770)), threaded=False)
