"""Protection, earthing and transformer selection (Stage 10, preliminary).

Rules are explicit, named and overridable; ratings are chosen from configurable standard series.
  * String overcurrent protection (IEC 62548-style): required when the reverse current that the other
    parallel strings can feed, (Np - 1) x 1.25 x Isc, exceeds the module's maximum series fuse rating.
    Fuse rating In within [1.5 x Isc, 2.4 x Isc] and <= module max series fuse rating.
  * AC breaker per inverter: In >= factor x inverter max AC current (factor default 1.25, company rule).
  * Protective earthing conductor (adiabatic, IEC 60364-5-54 form): S >= sqrt(I^2 t) / k.
  * Transformer: kVA >= AC kW / pf x margin, next rating in series.
Outputs are preliminary and require review by a qualified electrical engineer."""
from __future__ import annotations
import math

BREAKER_SERIES_A = [6, 10, 13, 16, 20, 25, 32, 40, 50, 63, 80, 100, 125, 160, 200, 250, 315, 400, 500, 630, 800, 1000, 1250, 1600, 2000, 2500, 3200, 4000]
FUSE_SERIES_A = [10, 12, 15, 16, 20, 25, 30, 32, 35, 40]
CONDUCTOR_SERIES_MM2 = [1.5, 2.5, 4, 6, 10, 16, 25, 35, 50, 70, 95, 120, 150, 185, 240, 300]
TRANSFORMER_SERIES_KVA = [16, 25, 63, 100, 160, 200, 250, 315, 400, 500, 630, 800, 1000, 1250, 1600, 2000, 2500]


def _next(series, v):
    return next((s for s in series if s >= v - 1e-9), None)


def string_protection(isc_a: float, module_max_fuse_a: float | None, parallel_strings: int, fuse_series=FUSE_SERIES_A):
    reverse = max(0, parallel_strings - 1) * 1.25 * isc_a
    if module_max_fuse_a is None:
        return {"required": None, "note": "Module max series fuse rating unknown: enter it from the datasheet"}
    req = reverse > module_max_fuse_a
    out = {"required": req, "reverse_current_a": round(reverse, 2), "module_max_series_fuse_a": module_max_fuse_a,
           "rule": "(Np-1) x 1.25 x Isc > module max series fuse rating"}
    if req:
        lo, hi = 1.5 * isc_a, min(2.4 * isc_a, module_max_fuse_a)
        pick = next((f for f in fuse_series if lo - 1e-9 <= f <= hi + 1e-9), None)
        out.update(fuse_a=pick, window_a=[round(lo, 2), round(hi, 2)],
                   **({} if pick else {"error": "No standard fuse inside the permitted window; review string configuration"}))
    return out


def ac_breaker(inverter_max_ac_a: float, factor=1.25, series=BREAKER_SERIES_A):
    need = inverter_max_ac_a * factor
    return {"design_current_a": round(need, 2), "rating_a": _next(series, need), "rule": f"In >= {factor} x inverter max AC current"}


def earthing_conductor(fault_current_a: float, disconnect_time_s: float, k: float, series=CONDUCTOR_SERIES_MM2):
    s = math.sqrt(fault_current_a ** 2 * disconnect_time_s) / k
    return {"min_area_mm2": round(s, 2), "size_mm2": _next(series, s), "rule": "S >= sqrt(I^2 t) / k (adiabatic)",
            "inputs": {"fault_current_a": fault_current_a, "t_s": disconnect_time_s, "k": k}}


def transformer(ac_kw: float, pf=0.95, margin=1.1, series=TRANSFORMER_SERIES_KVA):
    kva = ac_kw / pf * margin
    return {"required_kva": round(kva, 1), "rating_kva": _next(series, kva), "rule": f"kVA >= AC kW / {pf} x {margin}"}


def protection_design(electrical: dict, module: dict, *, grid_voltage_v=415.0, pf=1.0, ac_factor=1.25,
                      earth_fault_a=None, earth_t_s=None, earth_k=None, transformer_needed=False, tx_pf=0.95, tx_margin=1.1):
    if not electrical or "allocation" not in electrical:
        return {"status": "pending", "reason": "needs a string design"}
    par: dict = {}
    for a in electrical["allocation"]:
        par[(a["inverter"], a["mppt"])] = par.get((a["inverter"], a["mppt"]), 0) + 1
    worst = max(par.values())
    inv_kw = electrical["ac_kw"] / electrical["inverter_count"]
    i_ac = inv_kw * 1000 / (math.sqrt(3) * grid_voltage_v * pf)
    out = {"strings_per_mppt_max": worst,
           "string_protection": string_protection(module["isc_a"], module.get("max_series_fuse_a"), worst),
           "inverter_ac_current_a": round(i_ac, 2),
           "ac_breaker_per_inverter": ac_breaker(i_ac, ac_factor),
           "main_breaker": ac_breaker(i_ac * electrical["inverter_count"], ac_factor),
           "approval": "PRELIMINARY - ratings require verification by a qualified electrical engineer"}
    if None not in (earth_fault_a, earth_t_s, earth_k):
        out["earthing_conductor"] = earthing_conductor(earth_fault_a, earth_t_s, earth_k)
    else:
        out["earthing_conductor"] = {"status": "pending", "reason": "enter prospective earth-fault current, disconnection time and k"}
    if transformer_needed:
        out["transformer"] = transformer(electrical["ac_kw"], tx_pf, tx_margin)
    return out
