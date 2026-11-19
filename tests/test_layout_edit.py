from backend.engine.layout import LayoutSpec, place_modules
from backend.engine.layout_edit import validate_layout, move_module, compare_scenarios
from backend.engine.pipeline import run_design, legacy_to_request

ROOF = [(0, 0), (20, 0), (20, 12), (0, 12)]
OBS = [{"name": "Tank", "polygon": [(8, 5), (10, 5), (10, 7), (8, 7)], "height_m": 2}]


def test_auto_layout_validates_and_edits_are_checked():
    lay = place_modules(LayoutSpec(roof=ROOF, obstacles=[], setback_m=0.5, obstacle_clearance_m=0.6))
    fps = {p["id"]: p["footprint"] for p in lay["panels"]}
    assert validate_layout(ROOF, [], fps, setback_m=0.5, clearance_m=0.6)["valid"]
    ids = list(fps)
    bad = dict(fps)
    bad[ids[0]] = move_module(fps[ids[0]], -0.4, 0)                     # into setback
    bad[ids[1]] = [list(p) for p in fps[ids[2]]]                         # stacked on another module
    r = validate_layout(ROOF, OBS, bad, setback_m=0.5, clearance_m=0.6, locked={ids[1]})
    assert not r["valid"] and "setback" in r["invalid"][ids[0]][0]
    assert any("overlaps" in w for w in r["invalid"][ids[1]]) and r["locked_with_issues"] == [ids[1]]
    assert any("Tank" in w for v in r["invalid"].values() for w in v)    # auto layout ignored the tank -> flagged
    removed = {k: v for k, v in fps.items() if k not in r["invalid"]}
    assert validate_layout(ROOF, OBS, removed, setback_m=0.5, clearance_m=0.6)["valid"]


def test_scenario_comparison():
    base = {"name": "S", "latitude": 17.385, "longitude": 78.487, "roof": [{"x": x, "y": y} for x, y in ROOF]}
    rows = compare_scenarios({"flat": run_design(legacy_to_request(base)),
                              "tilt 15": run_design(legacy_to_request({**base, "tilt_deg": 15}))})
    assert {r["scenario"] for r in rows} == {"flat", "tilt 15"} and sum(r["most_capacity"] for r in rows) == 1
    flat, tilt = rows
    assert flat["modules"] >= tilt["modules"]                            # tilted rows need more spacing


def test_dpr_report():
    from backend.engine.exports import dpr_pdf
    from backend.engine.finance import capex_model
    from tests.test_engine_electrical_finance import INV
    from tests.test_engine_energy_boq_pipeline import W
    req = legacy_to_request({"name": "DPR", "latitude": 17.385, "longitude": 78.487, "roof": [{"x": x, "y": y} for x, y in ROOF]})
    req["inverter"] = {**INV.__dict__, "ac_kw": 20}; req["temps"] = {"min_ambient_c": 5, "max_cell_c": 70}
    r = run_design(req, weather=W)
    fin = capex_model(1_500_000, r["energy"]["annual_ac_kwh"], 8.0, degradation_pct=0.5, tariff_escalation_pct=2, om_year1=10000,
                      om_escalation_pct=5, discount_rate_pct=9)
    pdf = dpr_pdf(r, project={"name": "DPR test", "client": "Client", "location": "Hyderabad"}, company={"name": "Unite Solar"},
                  finance=fin, start_date="2026-12-01")
    assert pdf[:4] == b"%PDF" and len(pdf) > 50_000
    open("/tmp/dpr_test.pdf", "wb").write(pdf)
