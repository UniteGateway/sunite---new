"""API tests for equipment & geo routes. Need FastAPI; NOT executed in the authoring sandbox."""
from fastapi.testclient import TestClient
from backend.app import app
from tests.test_api import _login
from tests.test_geo_equipment_site import GOOD_MODULE

client = TestClient(app)


def test_geo_parse_location_route():
    r = client.post("/api/v3/geo/parse-location", json={"text": "17.385, 78.487"})
    assert r.status_code == 200 and r.json()["utm"]["zone"] == 44


def test_equipment_requires_auth_and_lists(tmp_path, monkeypatch):
    h = _login(tmp_path, monkeypatch)
    assert client.get("/api/equipment").status_code == 401
    r = client.post("/api/equipment", json={"kind": "module", "manufacturer": "T", "model": "M", "specs": GOOD_MODULE}, headers=h)
    assert r.status_code == 201, r.text
    assert len(client.get("/api/equipment", headers=h).json()) == 1
    assert client.post("/api/equipment", json={"kind": "module", "manufacturer": "T", "model": "M2",
                                               "specs": {**GOOD_MODULE, "vmp_v": 60}}, headers=h).status_code == 422
