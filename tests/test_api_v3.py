"""API-level tests for v0.3 routes. Require FastAPI/httpx (pip install -r requirements.txt).
NOT executed in the authoring sandbox (no package index access); run locally with `pytest -q`."""
from fastapi.testclient import TestClient
from backend.app import app
from backend.engine.pipeline import legacy_to_request

client = TestClient(app)
SITE = {"name": "API v3", "latitude": 17.385, "longitude": 78.487,
        "roof": [{"x": 0, "y": 0}, {"x": 30, "y": 0}, {"x": 30, "y": 18}, {"x": 0, "y": 18}],
        "obstacles": [{"name": "Room", "height_m": 2, "polygon": [{"x": 8, "y": 7}, {"x": 12, "y": 7},
                                                                  {"x": 12, "y": 10}, {"x": 8, "y": 10}]}]}


def test_v3_design_no_weather_no_energy():
    r = client.post("/api/v3/design", json=legacy_to_request(SITE))
    assert r.status_code == 200, r.text
    d = r.json()
    assert d["panel_count"] > 0 and d["energy"]["status"] == "weather_required"


def test_v3_rejects_bad_weather_and_bad_roof():
    bad = {**legacy_to_request(SITE), "weather_csv": "time_utc,ghi\n"}
    assert client.post("/api/v3/design", json=bad).status_code == 422
    req = legacy_to_request(SITE)
    req["site"]["roof"] = [{"x": 0, "y": 0}, {"x": 2, "y": 2}, {"x": 0, "y": 2}, {"x": 2, "y": 0}]
    assert client.post("/api/v3/design", json=req).status_code == 422


def test_v3_exports():
    for url, magic in [("/api/v3/export/report.pdf", b"%PDF"), ("/api/v3/export/boq.xlsx", b"PK")]:
        r = client.post(url, json=legacy_to_request(SITE))
        assert r.status_code == 200 and r.content[:len(magic)] == magic
    assert client.post("/api/export/report.pdf", json=SITE).content[:4] == b"%PDF"


def test_health_reports_engine_version():
    assert client.get("/api/health").json()["version"] == "0.3.0"
