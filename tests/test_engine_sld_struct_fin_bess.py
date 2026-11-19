"""Stage 11 SLD, 12 structural, 13 BOQ extensions, 15 commercial models, 16 BESS."""
import math
from backend.engine.layout import LayoutSpec, place_modules
from backend.engine.electrical import design_strings
from backend.engine.sld import build_sld, check_consistency
from backend.engine.structural import design_structure, G
from backend.engine.boq import build_boq, extra_lines, replace_allowances
from backend.engine.finance import (loan_schedule, financed_purchase, ppa_model, group_captive_model, metering_split,
                                    npv, irr, emi)
from backend.engine.bess import simulate_dispatch, size_sweep
from backend.engine.pipeline import run_design, legacy_to_request
from tests.test_engine_electrical_finance import MOD, INV, T
from tests.test_engine_energy_boq_pipeline import W
from tests.test_identity_tenancy import _raises

STRUCT = {"max_support_spacing_m": 2.0, "sections_kg_per_m": {"rail": 1.2, "rafter": 3.0, "leg": 2.5, "brace": 1.5},
          "anchors_per_leg": 2, "uplift_safety_factor": 1.5,
          "wind": {"vb_ms": 44, "k1": 1.0, "k2": 1.0, "k3": 1.0, "k4": 1.0, "cp_net_uplift": -1.2, "cp_net_down": 0.8}}  # TEST inputs


def _design(n=134):
    return design_strings(n, MOD, INV, T)


def test_sld_matches_design_and_exports():
    e = _design()
    s = build_sld(e, project="Test", module_model="TEST-550", interconnection={"transformer_kva": 250, "hv_kv": 11},
                  bess={"energy_kwh": 100, "power_kw": 50})
    assert check_consistency(s, e) == []
    assert s["string_count_drawn"] == e["string_count"] and s["inverters_drawn"] == e["inverter_count"]
    tags = {r["tag"] for r in s["schedule"]}
    assert {"ACDB-01", "M-01", "TX-01", "BESS-01", "EARTH"} <= tags
    assert sum(1 for r in s["schedule"] if r["type"] == "PV string") == e["string_count"]
    assert s["svg"].startswith("<svg") and s["svg"].endswith("</svg>") and "NOT FOR CONSTRUCTION" in s["svg"]
    assert "SECTION" in s["dxf"] and s["dxf"].rstrip().endswith("EOF")
    from backend.engine.geo import import_dxf   # our own DXF must be readable by our parser
    assert "ENTITIES" in s["dxf"]
    bad = dict(e, string_count=e["string_count"] + 1)
    assert check_consistency(s, bad)
    assert _raises(ValueError, build_sld, {"status": "fail"}, project="x", module_model="m")


def test_structure_hand_calc():
    lay = place_modules(LayoutSpec(roof=[(0, 0), (12, 0), (12, 8), (0, 8)], tilt_deg=10))
    st = design_structure(lay, module_length_m=2.278, module_width_m=1.134, module_weight_kg=28, tilt_deg=10,
                          system="rcc_anchored", spec=STRUCT)
    seg = lay["segments"][0]
    frames0 = math.ceil(seg["length_m"] / 2.0) + 1
    assert st["segments"][0]["frames"] == frames0 and st["segments"][0]["actual_spacing_m"] <= 2.0
    assert abs(st["loads"]["pz_n_m2"] - 0.6 * 44 ** 2) < 0.1                      # 1161.6 N/m2
    trib = 2.0 * 2.278
    assert abs(st["loads"]["uplift_per_frame_n"] - 0.6 * 44 ** 2 * 1.2 * trib) < 0.5
    rail = sum(2 * s["length_m"] for s in lay["segments"])
    assert abs(st["member_lengths_m"]["rail"] - rail) < 0.02
    assert abs(st["steel_kg_by_member"]["rail"] - rail * 1.2) < 0.1
    assert st["anchors"]["count"] == st["frames"] * 2 * 2
    assert any("PRELIMINARY" in f for f in st["flags"]) and st["approval"].startswith("NOT APPROVED")
    b = design_structure(lay, module_length_m=2.278, module_width_m=1.134, module_weight_kg=28, tilt_deg=10,
                         system="rcc_ballast", spec={**STRUCT, "ballast_unit_kg": 25, "roof_capacity_kn_m2": 0.3})
    need = max(0, b["loads"]["net_uplift_per_frame_n"]) * 1.5 / G
    assert b["ballast"]["blocks_per_frame"] == math.ceil(need / 25)
    assert any("exceeds stated roof capacity" in f for f in b["flags"])
    assert _raises(ValueError, design_structure, lay, module_length_m=2.278, module_width_m=1.134, module_weight_kg=28,
                   tilt_deg=10, spec={"max_support_spacing_m": 2})


def test_boq_extra_lines_replace_allowances():
    lay = place_modules(LayoutSpec(roof=[(0, 0), (30, 0), (30, 18), (0, 18)]))
    e = _design(len(lay["panels"]))
    s = build_sld(e, project="T", module_model="M")
    st = design_structure(lay, module_length_m=2.278, module_width_m=1.134, module_weight_kg=28, tilt_deg=0, spec=STRUCT)
    base = build_boq(lay, e, "M", "I")
    ex = extra_lines(structural=st, electrical=e, sld=s, ac={"route_m": 25, "runs": 2, "size_mm2": 35},
                     rules={"earth_pits_per_100kwp": 3, "monitoring_per_inverter": 1, "labour_hours_per_kwp": 6}, dc_kwp=73.7)
    full = replace_allowances(base, ex)
    codes = {l["code"] for l in full}
    assert {"MS-STEEL", "MS-ANCH", "EL-DCSPD", "AC-CAB", "EL-EPIT", "MON", "SV-LAB"} <= codes
    assert "MS-STR" not in codes and "EL-EARTH" not in codes                      # quantified now
    q = {l["code"]: l["qty"] for l in full}
    assert q["EL-DCSPD"] == e["inverter_count"] and q["AC-CAB"] == 50 and q["EL-EPIT"] == 3
    assert all(l["source"] and l["basis"] for l in full)


def test_loan_and_financed_purchase():
    s = loan_schedule(1_000_000, 10, 5)
    assert abs(sum(r["principal"] for r in s["rows"]) - 1_000_000) < 0.05 and s["rows"][-1]["closing_balance"] < 0.05
    assert abs(s["monthly_emi"] - emi(1_000_000, 10, 60)) < 0.01
    f = financed_purchase(1_000_000, 160_000, down_payment_pct=20, loan_rate_pct=10, loan_years=5, degradation_pct=0.5,
                          tariff_escalation_pct=2, om_year1=8000, om_escalation_pct=5, discount_rate_pct=9)
    assert f["loan_amount"] == 800_000 and f["cashflows"][0] == -200_000
    assert f["cashflows"][1] < f["cashflows"][6]                                  # EMI years vs debt-free years


def test_ppa_boot_reconciles():
    m = ppa_model(5_000_000, 150_000, ppa_tariff=5.5, ppa_escalation_pct=0, grid_tariff=9, grid_escalation_pct=2,
                  term_years=15, degradation_pct=0.5, om_year1=60_000, om_escalation_pct=4, developer_discount_pct=10,
                  customer_discount_pct=9, debt_pct=70, debt_rate_pct=10, debt_years=10)
    E = [150_000 * 0.995 ** y for y in range(15)]
    assert abs(m["ppa_revenue_total"] - sum(e * 5.5 for e in E)) < 1
    assert len(m["developer_cashflows"]) == 16 and len(m["customer_cashflows"]) == 26
    assert m["customer_cashflows"][16] > m["customer_cashflows"][15]              # free power after transfer
    assert m["min_dscr"] is not None and m["debt_amount"] == 3_500_000
    resco = ppa_model(5_000_000, 150_000, ppa_tariff=5.5, ppa_escalation_pct=0, grid_tariff=9, grid_escalation_pct=2,
                      term_years=15, degradation_pct=0.5, om_year1=60_000, om_escalation_pct=4, developer_discount_pct=10,
                      customer_discount_pct=9, boot_transfer=False)
    assert resco["customer_cashflows"][20] == 0 and resco["model"] == "RESCO/OPEX"


def test_group_captive_and_metering():
    gc = group_captive_model(10_000_000, 1_500_000, consumer_equity_pct=26, min_equity_pct=26, landed_charges_per_kwh=1.5,
                             generation_tariff=3.0, grid_tariff=8.0, grid_escalation_pct=0, degradation_pct=0,
                             years=10, discount_rate_pct=10)
    assert gc["eligible_equity"] and gc["landed_cost_per_kwh"] == 4.5 and gc["equity_investment"] == 2_600_000
    assert abs(gc["cashflows"][1] - 1_500_000 * 3.5) < 1
    m = metering_split(100_000, 70, "net", 8, 3)
    assert m["year1_value"] == 70_000 * 8 + 30_000 * 3
    z = metering_split(100_000, 70, "zero_export", 8)
    assert z["curtailed_kwh"] == 30_000 and z["year1_value"] == 560_000
    assert _raises(ValueError, metering_split, 1, 1, "magic", 1)


def _profiles():
    pv = [max(0.0, 50 * math.sin(math.pi * (h % 24 - 6) / 12)) for h in range(24 * 30)]
    load = [20 + (15 if 18 <= h % 24 <= 22 else 0) for h in range(24 * 30)]
    return pv, load


def test_bess_energy_balance_and_benefit():
    pv, load = _profiles()
    none = simulate_dispatch(pv, load, energy_kwh=0, power_kw=0)
    r = simulate_dispatch(pv, load, energy_kwh=100, power_kw=50, rte_pct=90)
    for x in (none, r):
        assert abs(x["energy_balance_error_kwh"]) < 1e-6 and abs(x["battery_balance_error_kwh"]) < 1e-6
    assert r["self_sufficiency_pct"] > none["self_sufficiency_pct"]
    assert r["totals_kwh"]["import"] < none["totals_kwh"]["import"]
    assert r["totals_kwh"]["discharge_out"] < r["totals_kwh"]["charge_in"]          # losses
    z = simulate_dispatch(pv, load, energy_kwh=0, power_kw=0, export_limit_kw=0)
    assert z["totals_kwh"]["export"] == 0 and z["totals_kwh"]["curtailed"] > 0 and abs(z["energy_balance_error_kwh"]) < 1e-6
    ps = simulate_dispatch([0.0] * 48, [30 if h % 24 in (19, 20) else 10 for h in range(48)], energy_kwh=60, power_kw=25,
                           strategy="peak_shaving", peak_threshold_kw=15, grid_charge=True, soc_init_pct=10)
    assert ps["peak_import_kw"] <= 15 + 1e-6 and abs(ps["energy_balance_error_kwh"]) < 1e-6
    sweep = size_sweep(pv, load, [0, 50, 100, 200])
    ss = [s["self_sufficiency_pct"] for s in sweep]
    assert ss == sorted(ss)                                                          # monotonic non-decreasing


def test_pipeline_full_engineering_chain():
    req = legacy_to_request({"name": "Full", "latitude": 17.385, "longitude": 78.487,
                             "roof": [{"x": 0, "y": 0}, {"x": 40, "y": 0}, {"x": 40, "y": 20}, {"x": 0, "y": 20}]})
    req["module"].update(verified=True, weight_kg=28)
    req["inverter"] = {**INV.__dict__}
    req["temps"] = {"min_ambient_c": 5, "max_cell_c": 70}
    req["cables"] = {"inverter_xy": [0, 0], "acdb_xy": [0, -10]}
    req["structure"] = {**STRUCT, "system": "rcc_anchored"}
    req["interconnection"] = {"grid_voltage_v": 415, "metering": "net"}
    req["bess"] = {"energy_kwh": 100, "power_kw": 50}
    req["load_kw"] = [40.0] * 8760
    req["boq_rules"] = {"earth_pits_per_100kwp": 3}
    r = run_design(req, weather=W)
    assert r["sld"]["consistency_issues"] == [] and r["structural"]["steel_tonnes"] > 0
    assert abs(r["bess"]["energy_balance_error_kwh"]) < 1e-3
    assert r["electrical"]["ac_cable"]["size_mm2"] is not None
    codes = {l["code"] for l in r["boq"]}
    assert {"MS-STEEL", "EL-DCSPD", "AC-CAB", "EL-EPIT"} <= codes
