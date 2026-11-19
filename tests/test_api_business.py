"""API tests for CRM / procurement / EPC / finance / BESS / report routes. Need FastAPI; NOT executed in the authoring sandbox."""
from fastapi.testclient import TestClient
from backend.app import app
from tests.test_api import _login

client = TestClient(app)


def test_crm_flow_routes(tmp_path, monkeypatch):
    h = _login(tmp_path, monkeypatch)
    c = client.post("/api/crm/customers", json={"name": "ABC", "segment": "industrial"}, headers=h)
    assert c.status_code == 200, c.text
    l = client.post("/api/crm/leads", json={"customer_id": c.json()["id"], "title": "ABC 50 kW"}, headers=h).json()
    assert client.post(f"/api/crm/leads/{l['id']}/stage", json={"stage": "contacted"}, headers=h).status_code == 200
    assert client.post(f"/api/crm/leads/{l['id']}/stage", json={"stage": "quoted"}, headers=h).status_code == 409
    assert client.post("/api/crm/customers", json={"name": "X", "bogus": 1}, headers=h).status_code == 422
    assert client.get("/api/crm/pipeline", headers=h).json()["stages"]["contacted"]["count"] == 1


def test_calculators():
    r = client.post("/api/v3/finance/loan", json={"principal": 100000, "annual_rate_pct": 10, "years": 2})
    assert r.status_code == 200 and r.json()["monthly_emi"] > 0
    assert client.post("/api/v3/finance/unknown", json={}).status_code == 404
    b = client.post("/api/v3/bess/dispatch", json={"pv_kw": [0, 5, 10, 5, 0], "load_kw": [3] * 5, "energy_kwh": 10, "power_kw": 5})
    assert b.status_code == 200 and abs(b.json()["energy_balance_error_kwh"]) < 1e-6
