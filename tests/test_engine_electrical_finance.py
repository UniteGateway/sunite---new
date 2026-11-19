import math
from backend.engine.electrical import (ModuleSpec, InverterSpec, SiteTemps, string_limits, design_strings,
                                       dc_voltage_drop_pct, ac3_voltage_drop_pct, size_for_voltage_drop)
from backend.engine.finance import emi, npv, irr, payback_years, capex_model

# Test fixtures only - illustrative values, NOT a product record.
MOD = ModuleSpec(model="TEST-550", pmax_w=550, voc_v=49.9, vmp_v=41.7, isc_a=14.0, imp_a=13.19,
                 beta_voc_pct=-0.28, gamma_pmax_pct=-0.35, alpha_isc_pct=0.05, verified=True)
INV = InverterSpec(model="TEST-60K", ac_kw=60, max_dc_v=1100, mppt_min_v=200, mppt_max_v=1000, mppt_count=4,
                   strings_per_mppt=2, max_input_current_per_mppt_a=30, max_isc_per_mppt_a=40, verified=True)
T = SiteTemps(min_ambient_c=5, max_cell_c=70)


def test_string_limits_hand_calc():
    lim = string_limits(MOD, INV, T)
    assert abs(lim["voc_cold_v"] - 52.694) < 1e-3          # 49.9*(1+0.0028*20)
    assert abs(lim["vmp_hot_v"] - 35.132) < 1e-3           # 41.7*(1-0.0035*45)
    assert lim["max_series_by_voc"] == 20                  # floor(1100/52.694)
    assert lim["min_series_by_mppt"] == 6                  # ceil(200/35.132)
    assert lim["max_series_by_mppt"] == 22                 # floor(1000/(41.7*1.07))
    assert lim["max_parallel_strings_per_mppt"] == 2


def test_design_strings_passes_and_allocates():
    d = design_strings(134, MOD, INV, T)
    assert d["status"] == "pass", d["checks"]
    assert d["modules_in_design"] + d["modules_unstrung"] == 134
    assert d["modules_per_string"] * d["string_count"] == d["modules_in_design"]
    per = {}
    for a in d["allocation"]:
        per[(a["inverter"], a["mppt"])] = per.get((a["inverter"], a["mppt"]), 0) + 1
    assert max(per.values()) <= 2
    assert 0.9 <= d["dc_ac_ratio"] <= 1.3


def test_design_strings_rejects_incompatible_pair():
    hv = InverterSpec(**{**INV.__dict__, "mppt_min_v": 900, "mppt_max_v": 950})
    assert design_strings(100, MOD, hv, T)["status"] == "fail"
    low_i = InverterSpec(**{**INV.__dict__, "max_input_current_per_mppt_a": 10})
    assert design_strings(100, MOD, low_i, T)["status"] == "fail"


def test_voltage_drop_hand_calc():
    assert abs(dc_voltage_drop_pct(50, 13, 800, 4) - 0.838) < 0.002
    vd = ac3_voltage_drop_pct(100, 100, 415, 50, pf=1.0, temp_c=20)
    assert abs(vd - 100 * math.sqrt(3) * 100 * 100 * 0.017241 / 50 / 415) < 1e-9
    r = size_for_voltage_drop("dc", 120, 13, 700, 1.0)      # 6 mm2 gives 1.53 %, 10 mm2 gives 0.92 %
    assert r["size_mm2"] == 10 and "NOT CHECKED" in r["ampacity_check"]
    r2 = size_for_voltage_drop("dc", 120, 13, 700, 1.0, ampacity_table={"10": 12, "16": 60})
    assert r2["size_mm2"] == 16 and r2["ampacity_check"] == "pass"


def test_finance_reference_values():
    assert abs(emi(1_000_000, 12, 12) - 88848.79) < 0.01
    assert abs(irr([-100, 110]) - 10) < 1e-6
    assert abs(irr([-1000, 500, 500, 500]) - 23.375) < 0.01
    assert abs(npv(10, [-100, 110])) < 1e-9
    assert abs(payback_years([-100, 30, 30, 30, 30]) - 10 / 3) < 1e-9
    assert payback_years([-100, 10, 10]) is None


def test_capex_model_reconciles():
    m = capex_model(1_000_000, 15_000, 8.0, degradation_pct=0.5, tariff_escalation_pct=2,
                    om_year1=10_000, om_escalation_pct=5, discount_rate_pct=9, inverter_replacement=(12, 80_000))
    assert len(m["table"]) == 25
    assert abs(m["npv"] - npv(9, m["cashflows"])) < 0.01
    exact = irr(m["cashflows"])
    assert abs(m["irr_pct"] - exact) < 0.001 and abs(npv(exact, m["cashflows"])) < 1e-3
    assert 5 < exact < 15
    assert m["table"][11]["replacement"] == 80_000
    assert abs(m["table"][1]["energy_kwh"] - 15_000 * 0.995) < 0.1
