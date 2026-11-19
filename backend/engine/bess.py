"""Hourly battery dispatch (Stage 16). Energy balance is enforced and reported every run.

Balance per step:  pv + grid_import + discharge = load + grid_export + charge + curtailed
Battery: charge efficiency = discharge efficiency = sqrt(round-trip efficiency).
Strategies: self_consumption, peak_shaving (threshold_kw), backup reserve (reserve_soc_pct)."""
from __future__ import annotations
import math


def simulate_dispatch(pv_kw: list[float], load_kw: list[float], *, energy_kwh, power_kw, rte_pct=90.0,
                      soc_min_pct=10.0, soc_max_pct=95.0, soc_init_pct=50.0, reserve_soc_pct=0.0,
                      strategy="self_consumption", peak_threshold_kw=None, export_limit_kw=None,
                      import_limit_kw=None, grid_charge=False, dt_h=1.0, fade_pct_per_cycle=0.0) -> dict:
    if len(pv_kw) != len(load_kw):
        raise ValueError("pv and load series must have equal length")
    if energy_kwh < 0 or power_kw < 0 or not 0 < rte_pct <= 100:
        raise ValueError("invalid battery parameters")
    if strategy == "peak_shaving" and peak_threshold_kw is None:
        raise ValueError("peak_shaving needs peak_threshold_kw")
    eta = math.sqrt(rte_pct / 100)
    cap = energy_kwh
    lo = cap * max(soc_min_pct, reserve_soc_pct) / 100
    soc = cap * soc_init_pct / 100
    T = {k: 0.0 for k in ("pv", "load", "import", "export", "charge_in", "discharge_out", "curtailed", "losses")}
    peak_after = 0.0
    throughput = 0.0
    unserved_peak = 0
    for pv, ld in zip(pv_kw, load_kw):
        hi = cap * soc_max_pct / 100
        net = pv - ld                                     # kW
        ch = dis = 0.0
        if net > 0:                                       # surplus -> charge
            ch = min(net, power_kw, max(0.0, (hi - soc)) / (eta * dt_h))
        else:
            deficit = -net
            target = deficit if strategy != "peak_shaving" else max(0.0, deficit - peak_threshold_kw)
            dis = min(target, power_kw, max(0.0, (soc - lo)) * eta / dt_h)
        if grid_charge and strategy == "peak_shaving" and net <= 0:
            headroom = peak_threshold_kw - (-net)
            if headroom > 0 and soc < hi:
                ch = min(headroom, power_kw, (hi - soc) / (eta * dt_h)); dis = 0.0
        soc += (ch * eta - dis / eta) * dt_h
        grid = ld - pv + ch - dis                         # + import / - export
        imp, exp = max(0.0, grid), max(0.0, -grid)
        curt = 0.0
        if export_limit_kw is not None and exp > export_limit_kw:
            curt = exp - export_limit_kw; exp = export_limit_kw
        if import_limit_kw is not None and imp > import_limit_kw + 1e-9:
            unserved_peak += 1
        T["pv"] += pv * dt_h; T["load"] += ld * dt_h; T["import"] += imp * dt_h; T["export"] += exp * dt_h
        T["charge_in"] += ch * dt_h; T["discharge_out"] += dis * dt_h; T["curtailed"] += curt * dt_h
        T["losses"] += (ch * (1 - eta) + dis * (1 / eta - 1)) * dt_h
        throughput += dis * dt_h
        peak_after = max(peak_after, imp)
        if fade_pct_per_cycle and cap:
            cap *= 1 - fade_pct_per_cycle / 100 * (dis * dt_h / (energy_kwh or 1))
            soc = min(soc, cap * soc_max_pct / 100)
    soc_end = soc
    lhs = T["pv"] + T["import"] + T["discharge_out"]
    rhs = T["load"] + T["export"] + T["charge_in"] + T["curtailed"]
    stored_change = soc_end - energy_kwh * soc_init_pct / 100
    cycles = throughput / energy_kwh if energy_kwh else 0.0
    pv_used = T["pv"] - T["export"] - T["curtailed"]
    return {
        "strategy": strategy, "totals_kwh": {k: round(v, 3) for k, v in T.items()},
        "energy_balance_error_kwh": round(lhs - rhs, 6),
        "battery_balance_error_kwh": round(T["charge_in"] - T["discharge_out"] - T["losses"] - stored_change, 6)
        if not fade_pct_per_cycle else None,
        "self_consumption_pct": round(100 * pv_used / T["pv"], 2) if T["pv"] else None,
        "self_sufficiency_pct": round(100 * (T["load"] - T["import"]) / T["load"], 2) if T["load"] else None,
        "equivalent_full_cycles": round(cycles, 2), "peak_import_kw": round(peak_after, 3),
        "peak_net_load_kw_without_bess": round(max((l - p for p, l in zip(pv_kw, load_kw)), default=0), 3),
        "hours_import_limit_exceeded": unserved_peak, "end_capacity_kwh": round(cap, 3),
        "assumptions": {"rte_pct": rte_pct, "soc_window_pct": [soc_min_pct, soc_max_pct], "reserve_soc_pct": reserve_soc_pct,
                        "power_kw": power_kw, "energy_kwh": energy_kwh, "dt_h": dt_h, "fade_pct_per_cycle": fade_pct_per_cycle},
    }


def size_sweep(pv_kw, load_kw, energies_kwh, c_rate=0.5, **kw):
    """Self-sufficiency vs battery size, for choosing a capacity."""
    return [{"energy_kwh": e, "power_kw": e * c_rate,
             **{k: v for k, v in simulate_dispatch(pv_kw, load_kw, energy_kwh=e, power_kw=e * c_rate, **kw).items()
                if k in ("self_sufficiency_pct", "self_consumption_pct", "equivalent_full_cycles", "peak_import_kw")}}
            for e in energies_kwh]
