import math
from datetime import datetime, timedelta, timezone
from backend.engine.energy import poa_isotropic, simulate, parse_weather_csv
from backend.engine.shading import clear_sky_dni
from backend.engine.solar import sun_position
from backend.engine.layout import LayoutSpec, place_modules
from backend.engine.boq import build_boq, price_boq
from backend.engine.pipeline import run_design, legacy_to_request


def synthetic_clear_year(lat, lon):
    """TEST FIXTURE ONLY: a clear-sky year from the Meinel model. Not real weather."""
    rows, t = [], datetime(2026, 1, 1, tzinfo=timezone.utc)
    for _ in range(8760):
        sp = sun_position(t + timedelta(minutes=30), lat, lon)
        dni = clear_sky_dni(sp.zenith)
        dhi = 0.1 * dni if dni else 0.0
        ghi = dni * max(0, math.cos(math.radians(sp.zenith))) + dhi
        rows.append({"time": t + timedelta(minutes=30), "ghi": ghi, "dni": dni, "dhi": dhi, "temp_air": 28.0})
        t += timedelta(hours=1)
    return rows


W = synthetic_clear_year(17.385, 78.487)


def test_horizontal_poa_equals_ghi():
    b, s, gnd, _ = poa_isotropic(800, 700, 100, 30, 150, 0, 180)
    assert abs((b + s + gnd) - (700 * math.cos(math.radians(30)) + 100)) < 1e-9


def test_no_weather_no_numbers():
    r = simulate(None, latitude=17, longitude=78, tilt=10, azimuth=180, dc_kwp=10, ac_kw=8, gamma_pmax_pct=-0.35)
    assert r["status"] == "weather_required" and "annual_ac_kwh" not in r


def test_simulation_physics():
    kw = dict(latitude=17.385, longitude=78.487, tilt=15, azimuth=180, dc_kwp=100, gamma_pmax_pct=-0.35)
    base = simulate(W, ac_kw=90, **kw)
    assert 0.6 < base["performance_ratio"] < 0.95
    assert abs(sum(base["monthly_ac_kwh"]) - base["annual_ac_kwh"]) < 1
    shaded = simulate(W, ac_kw=90, beam_shading_pct=10, **kw)
    loss = 1 - shaded["annual_ac_kwh"] / base["annual_ac_kwh"]
    assert 0 < loss < 0.10                         # beam-only: never more than the beam fraction
    clipped = simulate(W, ac_kw=40, **kw)
    assert clipped["clipping_kwh"] > 0 and clipped["annual_ac_kwh"] < base["annual_ac_kwh"]
    assert max(clipped["monthly_ac_kwh"]) <= 40 * 24 * 31


def _raises(fn, exc=ValueError):
    try:
        fn()
    except exc:
        return True
    return False


def test_weather_csv_validation():
    assert _raises(lambda: parse_weather_csv("time_utc,ghi\n2026-01-01T00:00Z,0\n"))
    hdr = "time_utc,ghi,dni,dhi,temp_air\n"
    assert _raises(lambda: parse_weather_csv(hdr + "2026-01-01T00:00Z,0,0,0,20\n"))   # too short
    rows = "".join(f"{w['time'].isoformat()},{w['ghi']},{w['dni']},{w['dhi']},{w['temp_air']}\n" for w in W)
    parsed = parse_weather_csv(hdr + rows)
    assert len(parsed) == 8760
    assert _raises(lambda: parse_weather_csv(hdr + rows.replace(",28.0", ",28.0", 1).replace(
        rows.splitlines()[0], rows.splitlines()[0].replace(",28.0", ",28.0").split(",")[0] + ",-5,0,0,28")))


def test_boq_traceability_and_pricing():
    lay = place_modules(LayoutSpec(roof=[(0, 0), (20, 0), (20, 10), (0, 10)]))
    lines = build_boq(lay, None, "TEST", None)
    assert all(l["source"] and l["basis"] and l["confidence"] for l in lines)
    q = {l["code"]: l["qty"] for l in lines}
    assert q["PV-MOD"] == len(lay["panels"])
    assert abs(q["MS-RAIL"] - sum(2 * s["length_m"] for s in lay["segments"])) < 0.01
    t = price_boq(lines, {"PV-MOD": 10000, "MS-RAIL": 500}, {"material": 12}, freight_pct=2, contingency_pct=3)
    mat = q["PV-MOD"] * 10000 + q["MS-RAIL"] * 500
    expect = (mat + mat * 0.02 + mat * 0.12) * 1.03
    assert abs(t["grand_total_priced_items"] - expect) < 0.05
    assert not t["complete"] and "PV-INV" in t["unpriced_items"]
    assert [l for l in lines if l["code"] == "PV-INV"][0]["amount"] is None


def test_pipeline_legacy_and_north_rotation():
    site = {"name": "T", "latitude": 17.385, "longitude": 78.487,
            "roof": [{"x": 0, "y": 0}, {"x": 30, "y": 0}, {"x": 30, "y": 18}, {"x": 0, "y": 18}],
            "obstacles": [{"name": "Room", "height_m": 2,
                           "polygon": [{"x": 8, "y": 7}, {"x": 12, "y": 7}, {"x": 12, "y": 10}, {"x": 8, "y": 10}]}]}
    a = run_design(legacy_to_request(site))
    assert a["panel_count"] > 100 and a["energy"]["status"] == "weather_required"
    # same roof drawn rotated 90 deg with north arrow at 90 deg -> identical true-frame result
    rot = lambda p: {"x": -p["y"], "y": p["x"]}
    req = legacy_to_request({**site, "roof": [rot(p) for p in site["roof"]],
                             "obstacles": [{**site["obstacles"][0], "polygon": [rot(p) for p in site["obstacles"][0]["polygon"]]}]})
    req["site"]["north_rotation_deg"] = 90   # drawing +y axis points true east
    b = run_design(req)
    assert b["panel_count"] == a["panel_count"]
    assert abs(b["shading"]["beam_weighted_shading_pct"] - a["shading"]["beam_weighted_shading_pct"]) < 1e-6


def test_pipeline_full_chain_with_weather_and_finance():
    req = legacy_to_request({"name": "F", "latitude": 17.385, "longitude": 78.487,
                             "roof": [{"x": 0, "y": 0}, {"x": 40, "y": 0}, {"x": 40, "y": 20}, {"x": 0, "y": 20}]})
    req["module"].update(verified=True)
    req["inverter"] = {"model": "TEST-60K", "ac_kw": 60, "max_dc_v": 1100, "mppt_min_v": 200, "mppt_max_v": 1000,
                       "mppt_count": 4, "strings_per_mppt": 2, "max_input_current_per_mppt_a": 30,
                       "max_isc_per_mppt_a": 40, "verified": True}
    req["temps"] = {"min_ambient_c": 5, "max_cell_c": 70}
    req["cables"] = {"inverter_xy": [0, 0], "dc_vd_limit_pct": 1.5}
    codes = ["PV-MOD", "PV-INV", "DC-MC4", "DC-CAB", "MS-RAIL", "MS-ECL", "MS-MCL", "MS-STR", "EL-ACDB",
             "EL-SPD", "EL-EARTH", "SV-INST"]
    req["pricing"] = {"rates": {c: 100.0 for c in codes}}
    # lot allowances (structure, ACDB, SPD, earthing) have no quantity -> stay unpriced
    req["finance"] = {"tariff_per_kwh": 8, "degradation_pct": 0.5, "tariff_escalation_pct": 2, "om_year1": 1000,
                      "om_escalation_pct": 5, "discount_rate_pct": 9}
    r = run_design(req, weather=W)
    assert r["electrical"]["status"] == "pass"
    assert r["energy"]["status"] == "ok"
    assert r["electrical"]["dc_cable"]["size_mm2"] is not None
    assert r["finance"]["status"] == "blocked"     # lot allowances unpriced -> no fake totals
    assert r["boq_totals"]["complete"] is False


def test_legacy_response_contract():
    """Mirrors the v0.2 API tests so the existing UI keeps working on the v0.3 engine."""
    from backend.engine.pipeline import legacy_response
    site = {"name": "Test roof", "latitude": 17.385, "longitude": 78.487,
            "roof": [{"x": 0, "y": 0}, {"x": 30, "y": 0}, {"x": 30, "y": 18}, {"x": 0, "y": 18}],
            "obstacles": [{"name": "Room", "height_m": 2,
                           "polygon": [{"x": 8, "y": 7}, {"x": 12, "y": 7}, {"x": 12, "y": 10}, {"x": 8, "y": 10}]}],
            "material_rates_inr": {"PV modules": 9000}}
    d = legacy_response(site, run_design(legacy_to_request(site)))
    assert d["panel_count"] > 0 and len(d["panels"]) == d["panel_count"] and d["dc_kwp"] > 0
    assert d["shading_samples"] and d["electrical"]["max_modules_per_string_voltage"] > 0
    assert d["electrical"]["voltage_screen_pass"] is True
    assert d["priced_boq_subtotal_inr"] == d["panel_count"] * 9000
    for k in ("annual_kwh_before_detailed_shading", "preliminary_installed_cost_inr", "roof", "obstacles", "warnings"):
        assert k in d


def test_exports_xlsx_and_pdf(tmp_dir="/tmp"):
    import io
    from openpyxl import load_workbook
    from backend.engine.exports import boq_xlsx, design_report_pdf
    req = legacy_to_request({"name": "Export test", "latitude": 17.385, "longitude": 78.487,
                             "roof": [{"x": 0, "y": 0}, {"x": 30, "y": 0}, {"x": 30, "y": 18}, {"x": 0, "y": 18}],
                             "obstacles": [{"name": "Stair", "height_m": 3, "polygon": [{"x": 8, "y": 7}, {"x": 12, "y": 7},
                                                                                     {"x": 12, "y": 10}, {"x": 8, "y": 10}]}]})
    r = run_design(req, weather=W)
    wb = load_workbook(io.BytesIO(boq_xlsx(r)))
    assert {"BOQ", "Panel shading"} <= set(wb.sheetnames)
    rows = list(wb["BOQ"].values)
    assert any(row and row[0] == "PV-MOD" and row[4] == r["panel_count"] for row in rows)
    pdf = design_report_pdf(r)
    assert pdf[:5] == b"%PDF-" and len(pdf) > 20000
    open(f"{tmp_dir}/sunite_report_test.pdf", "wb").write(pdf)
