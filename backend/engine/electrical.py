"""DC string sizing, MPPT allocation and voltage-drop calculations.

All equipment values come from the caller (catalogue entries flagged as verified
against the manufacturer datasheet). Nothing here invents a specification.
Results are PRELIMINARY and never constitute construction approval.
"""
from __future__ import annotations
import math
from dataclasses import dataclass, asdict

RULESET = "SUNITE-ELEC-PRELIM-2026.10 (temperature-corrected IEC 62548-style voltage checks; "\
          "ampacity requires a configured jurisdiction table)"

RHO_CU_20 = 0.017241   # ohm.mm2/m, annealed copper at 20 C (IEC 60228 basis)
RHO_AL_20 = 0.028264
ALPHA_CU, ALPHA_AL = 0.00393, 0.00403
STD_SIZES_MM2 = [1.5, 2.5, 4, 6, 10, 16, 25, 35, 50, 70, 95, 120, 150, 185, 240, 300, 400]


@dataclass
class ModuleSpec:
    model: str
    pmax_w: float
    voc_v: float
    vmp_v: float
    isc_a: float
    imp_a: float
    beta_voc_pct: float        # %/C, negative
    gamma_pmax_pct: float      # %/C, negative (used for Vmp if beta_vmp missing)
    alpha_isc_pct: float = 0.05
    beta_vmp_pct: float | None = None
    max_system_v: float = 1500
    verified: bool = False


@dataclass
class InverterSpec:
    model: str
    ac_kw: float
    max_dc_v: float
    mppt_min_v: float
    mppt_max_v: float
    mppt_count: int
    strings_per_mppt: int
    max_input_current_per_mppt_a: float
    max_isc_per_mppt_a: float
    start_v: float = 0
    verified: bool = False


@dataclass
class SiteTemps:
    min_ambient_c: float        # record low used for cold Voc
    max_cell_c: float           # hottest expected cell temperature


def string_limits(m: ModuleSpec, inv: InverterSpec, t: SiteTemps) -> dict:
    voc_cold = m.voc_v * (1 + m.beta_voc_pct / 100 * (t.min_ambient_c - 25))
    beta_vmp = m.beta_vmp_pct if m.beta_vmp_pct is not None else m.gamma_pmax_pct
    vmp_hot = m.vmp_v * (1 + beta_vmp / 100 * (t.max_cell_c - 25))
    vmp_cold = m.vmp_v * (1 + beta_vmp / 100 * (t.min_ambient_c - 25))
    isc_hot = m.isc_a * (1 + m.alpha_isc_pct / 100 * (t.max_cell_c - 25))
    vlimit = min(inv.max_dc_v, m.max_system_v)
    n_max_voc = math.floor(vlimit / voc_cold)
    n_max_mppt = math.floor(inv.mppt_max_v / vmp_cold)
    n_min = math.ceil(max(inv.mppt_min_v, inv.start_v) / vmp_hot)
    n_max = min(n_max_voc, n_max_mppt)
    par_current = math.floor(inv.max_input_current_per_mppt_a / m.imp_a)
    par_isc = math.floor(inv.max_isc_per_mppt_a / isc_hot)
    par_max = max(0, min(inv.strings_per_mppt, par_current, par_isc))
    return {
        "voc_cold_v": round(voc_cold, 3), "vmp_hot_v": round(vmp_hot, 3), "vmp_cold_v": round(vmp_cold, 3),
        "isc_hot_a": round(isc_hot, 3), "system_voltage_limit_v": vlimit,
        "max_series_by_voc": n_max_voc, "max_series_by_mppt": n_max_mppt,
        "min_series_by_mppt": n_min, "series_range": [n_min, n_max] if n_min <= n_max else None,
        "max_parallel_strings_per_mppt": par_max,
        "beta_vmp_source": "datasheet beta_Vmp" if m.beta_vmp_pct is not None else "gamma_Pmax used as proxy",
    }


def design_strings(n_modules: int, m: ModuleSpec, inv: InverterSpec, t: SiteTemps,
                   dc_ac_max: float = 1.3, dc_ac_min: float = 0.9) -> dict:
    lim = string_limits(m, inv, t)
    checks, warnings = [], []
    if not m.verified or not inv.verified:
        warnings.append("Equipment record not marked as datasheet-verified: results are indicative only.")
    if lim["series_range"] is None:
        return {"status": "fail", "limits": lim, "ruleset": RULESET,
                "error": "No series length satisfies both cold-Voc and hot-MPPT limits for this module/inverter pair."}
    if lim["max_parallel_strings_per_mppt"] < 1:
        return {"status": "fail", "limits": lim, "ruleset": RULESET,
                "error": "Module current exceeds inverter MPPT input current."}
    lo, hi = lim["series_range"]
    best = None
    for ns in range(hi, lo - 1, -1):
        strings = n_modules // ns
        if strings == 0:
            continue
        used = strings * ns
        dc_kw = used * m.pmax_w / 1000
        per_inv_strings = inv.mppt_count * lim["max_parallel_strings_per_mppt"]
        n_inv = max(math.ceil(strings / per_inv_strings), math.ceil(dc_kw / (inv.ac_kw * dc_ac_max)))
        ratio = dc_kw / (n_inv * inv.ac_kw)
        out_of_band = ratio > dc_ac_max + 1e-9 or ratio < dc_ac_min - 1e-9
        key = (out_of_band, n_modules - used, abs(ratio - 1.2), -ns)
        cand = dict(series=ns, strings=strings, used=used, dc_kw=dc_kw, n_inv=n_inv, ratio=ratio)
        if best is None or key < best[0]:
            best = (key, cand)
    if best is None:
        return {"status": "fail", "limits": lim, "ruleset": RULESET,
                "error": f"Only {n_modules} modules: fewer than the minimum string length {lo}."}
    c = best[1]
    # Round-robin allocation of strings to inverter MPPTs
    allocation = []
    slots = [(i, k) for k in range(inv.mppt_count) for i in range(c["n_inv"])]
    counts: dict = {}
    for s in range(c["strings"]):
        inv_i, mppt = slots[s % len(slots)]
        counts[(inv_i, mppt)] = counts.get((inv_i, mppt), 0) + 1
        allocation.append({"string": f"S{s + 1:03d}", "inverter": inv_i + 1, "mppt": mppt + 1,
                           "modules": c["series"]})
    max_par = max(counts.values())
    vstring_cold = c["series"] * lim["voc_cold_v"]
    vmp_hot = c["series"] * lim["vmp_hot_v"]
    vmp_cold = c["series"] * lim["vmp_cold_v"]
    checks += [
        _chk("String Voc at min ambient", vstring_cold, "<=", lim["system_voltage_limit_v"], "V"),
        _chk("String Vmp hot within MPPT min", vmp_hot, ">=", inv.mppt_min_v, "V"),
        _chk("String Vmp cold within MPPT max", vmp_cold, "<=", inv.mppt_max_v, "V"),
        _chk("MPPT operating current", max_par * m.imp_a, "<=", inv.max_input_current_per_mppt_a, "A"),
        _chk("MPPT short-circuit current (hot)", max_par * lim["isc_hot_a"], "<=", inv.max_isc_per_mppt_a, "A"),
        _chk("DC/AC ratio upper bound", c["ratio"], "<=", dc_ac_max, ""),
        _chk("DC/AC ratio lower bound", c["ratio"], ">=", dc_ac_min, ""),
    ]
    if c["used"] < n_modules:
        warnings.append(f"{n_modules - c['used']} placed module(s) cannot form a valid string and are excluded from the electrical design.")
    status = "pass" if all(x["pass"] for x in checks) else "fail"
    return {"status": status, "ruleset": RULESET, "limits": lim,
            "module": m.model, "inverter": inv.model, "inverter_count": c["n_inv"],
            "modules_per_string": c["series"], "string_count": c["strings"],
            "modules_in_design": c["used"], "modules_unstrung": n_modules - c["used"],
            "dc_kwp": round(c["dc_kw"], 3), "ac_kw": round(c["n_inv"] * inv.ac_kw, 3),
            "dc_ac_ratio": round(c["ratio"], 3), "allocation": allocation,
            "checks": checks, "warnings": warnings,
            "approval": "PRELIMINARY - requires review and sign-off by a qualified electrical engineer"}


def _chk(name, value, op, limit, unit):
    ok = value <= limit + 1e-9 if op == "<=" else value >= limit - 1e-9
    return {"check": name, "value": round(value, 3), "operator": op, "limit": limit, "unit": unit, "pass": bool(ok)}


def resistivity(material: str, temp_c: float) -> float:
    if material == "cu":
        return RHO_CU_20 * (1 + ALPHA_CU * (temp_c - 20))
    if material == "al":
        return RHO_AL_20 * (1 + ALPHA_AL * (temp_c - 20))
    raise ValueError("material must be cu or al")


def dc_voltage_drop_pct(length_m, current_a, voltage_v, area_mm2, material="cu", temp_c=70.0):
    """Two-conductor DC circuit, one-way route length."""
    return 100 * 2 * length_m * current_a * resistivity(material, temp_c) / area_mm2 / voltage_v


def ac3_voltage_drop_pct(length_m, current_a, line_v, area_mm2, pf=1.0, material="cu",
                         temp_c=70.0, reactance_ohm_per_km=0.0):
    r = resistivity(material, temp_c) / area_mm2         # ohm/m
    x = reactance_ohm_per_km / 1000
    sinphi = math.sqrt(max(0, 1 - pf * pf))
    return 100 * math.sqrt(3) * length_m * current_a * (r * pf + x * sinphi) / line_v


def size_for_voltage_drop(kind, length_m, current_a, voltage_v, limit_pct, material="cu",
                          temp_c=70.0, min_size=4, pf=1.0, ampacity_table: dict | None = None):
    """Smallest standard size meeting the drop limit (and ampacity if a table is configured)."""
    for a in STD_SIZES_MM2:
        if a < min_size:
            continue
        vd = (dc_voltage_drop_pct(length_m, current_a, voltage_v, a, material, temp_c) if kind == "dc"
              else ac3_voltage_drop_pct(length_m, current_a, voltage_v, a, pf, material, temp_c))
        amp_ok = None
        if ampacity_table:
            cap = ampacity_table.get(str(a)) or ampacity_table.get(a)
            amp_ok = cap is not None and cap >= current_a
            if not amp_ok:
                continue
        if vd <= limit_pct:
            return {"size_mm2": a, "voltage_drop_pct": round(vd, 3), "limit_pct": limit_pct,
                    "ampacity_check": "pass" if amp_ok else "NOT CHECKED - no ampacity table configured for this jurisdiction"}
    return {"size_mm2": None, "error": "No standard size meets the limit; split the circuit or relocate equipment"}
