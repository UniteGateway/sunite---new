"""End-to-end design pipeline: plain dict in, plain dict out (framework-free, testable)."""
from __future__ import annotations
import math
from . import geometry as g
from .layout import LayoutSpec, Obstacle, place_modules
from .shading import analyse, shadow_polygons
from .electrical import ModuleSpec, InverterSpec, SiteTemps, design_strings, string_limits, size_for_voltage_drop
from .energy import simulate
from .boq import build_boq, price_boq, extra_lines, replace_allowances
from .sld import build_sld, check_consistency
from .structural import design_structure
from .bess import simulate_dispatch
from .protection import protection_design
from .finance import capex_model

ENGINE_VERSION = "0.3.0"
_MODULE_FIELDS = set(ModuleSpec.__dataclass_fields__)
_INV_FIELDS = set(InverterSpec.__dataclass_fields__)


def _to_true(pts, north_rot):
    """Rotate drawing coordinates so +y is true north. north_rot = bearing (deg, clockwise
    from true north) of the drawing's +y axis."""
    pts = [(float(p[0]), float(p[1])) if not isinstance(p, dict) else (float(p["x"]), float(p["y"])) for p in pts]
    return [g.rotate_pt(p, -north_rot) for p in pts] if north_rot else pts


def run_design(req: dict, weather: list | None = None) -> dict:
    site = req["site"]
    nr = site.get("north_rotation_deg", 0.0)
    roof = _to_true(site["roof"], nr)
    obstacles = [{**o, "polygon": _to_true(o["polygon"], nr)} for o in site.get("obstacles", [])]
    mod = req["module"]
    lay = req.get("layout", {})
    spec = LayoutSpec(
        roof=roof,
        obstacles=[Obstacle(polygon=o["polygon"], height_m=o.get("height_m", 1.0), name=o.get("name", "Obstacle"),
                            kind=o.get("kind", "obstacle"), clearance_m=o.get("clearance_m")) for o in obstacles],
        module_length_m=mod["length_m"], module_width_m=mod["width_m"],
        latitude=site["latitude"], longitude=site["longitude"],
        **{k: v for k, v in lay.items() if k in LayoutSpec.__dataclass_fields__ and k not in ("roof", "obstacles")})
    warnings = []
    layout = place_modules(spec)
    panels = layout["panels"]

    sh_cfg = req.get("shading", {})
    shading = analyse(panels, roof, [o for o in obstacles if o.get("kind", "obstacle") != "keepout"],
                      site["latitude"], site["longitude"], site.get("parapet_height_m", 0.0),
                      step_min=sh_cfg.get("step_min", 60), nu=sh_cfg.get("nu", 3), nv=sh_cfg.get("nv", 3),
                      include_row_shading=sh_cfg.get("include_row_shading", True))

    module = ModuleSpec(**{k: v for k, v in mod.items() if k in _MODULE_FIELDS})
    electrical = None
    if req.get("inverter"):
        inverter = InverterSpec(**{k: v for k, v in req["inverter"].items() if k in _INV_FIELDS})
        temps = SiteTemps(**req["temps"])
        electrical = design_strings(len(panels), module, inverter, temps,
                                    dc_ac_max=req.get("dc_ac_max", 1.3), dc_ac_min=req.get("dc_ac_min", 0.9))
        # bound for electrical shading impact, given string assignment in row order
        if electrical.get("modules_per_string") and shading.get("panel_shading_pct"):
            per = electrical["modules_per_string"]
            order = sorted(range(len(panels)), key=lambda i: (panels[i]["row"], panels[i]["col"]))
            pct = shading["panel_shading_pct"]
            worst = [max(pct[i] for i in order[s * per:(s + 1) * per]) for s in range(electrical["string_count"])]
            shading["string_worst_module_bound_pct"] = round(sum(worst) / len(worst), 3) if worst else 0.0
        cab = req.get("cables", {})
        if cab.get("inverter_xy") and electrical.get("status") in ("pass", "fail") and "limits" in electrical:
            ixy = _to_true([cab["inverter_xy"]], nr)[0]
            longest = max((abs(p["center"][0] - ixy[0]) + abs(p["center"][1] - ixy[1])) for p in panels) \
                * cab.get("dc_route_factor", 1.3)
            v_op = electrical["modules_per_string"] * module.vmp_v
            electrical["dc_cable"] = {"longest_route_m": round(longest, 1), **size_for_voltage_drop(
                "dc", longest, module.imp_a, v_op, cab.get("dc_vd_limit_pct", 1.5),
                ampacity_table=cab.get("ampacity_table"))}
    else:
        warnings.append("No inverter selected: string design, inverter quantity and DC cable sizing are pending.")

    dc_kwp = (electrical or {}).get("dc_kwp") or round(len(panels) * module.pmax_w / 1000, 3)
    ac_kw = (electrical or {}).get("ac_kw")
    energy = simulate(weather, latitude=site["latitude"], longitude=site["longitude"],
                      tilt=spec.tilt_deg, azimuth=spec.array_azimuth_deg, dc_kwp=dc_kwp,
                      ac_kw=ac_kw or dc_kwp / 1.2, gamma_pmax_pct=module.gamma_pmax_pct,
                      beam_shading_pct=shading.get("beam_weighted_shading_pct", 0.0),
                      losses_pct=req.get("losses_pct"), degradation_pct_per_year=req.get("degradation_pct"),
                      weather_source=req.get("weather_source", "user file"))
    if ac_kw is None and energy.get("status") == "ok":
        warnings.append("AC rating assumed DC/1.2 for clipping because no inverter is selected.")

    boq = build_boq(layout, electrical, module.model, (req.get("inverter") or {}).get("model"),
                    inverter_xy=_to_true([req["cables"]["inverter_xy"]], nr)[0] if req.get("cables", {}).get("inverter_xy") else None,
                    string_cable_mm2=((electrical or {}).get("dc_cable") or {}).get("size_mm2"))
    # ---- Stage 11/12/13/16 extensions (each optional, each traceable) ----
    sld = structural = bess = None
    if electrical and electrical.get("allocation") and electrical.get("status") in ("pass", "fail"):
        sld = build_sld(electrical, project=site.get("name", "Project"), module_model=module.model,
                        interconnection=req.get("interconnection"), bess=req.get("bess"))
        sld["consistency_issues"] = check_consistency(sld, electrical)
    if electrical and electrical.get("allocation"):
        pr_in = req.get("protection") or {}
        ic = req.get("interconnection") or {}
        electrical["protection"] = protection_design(
            electrical, mod, grid_voltage_v=ic.get("grid_voltage_v", 415), transformer_needed=bool(ic.get("transformer_kva") or pr_in.get("transformer_needed")),
            **{k: v for k, v in pr_in.items() if k in ("pf", "ac_factor", "earth_fault_a", "earth_t_s", "earth_k", "tx_pf", "tx_margin")})
    if req.get("structure"):
        st = req["structure"]
        structural = design_structure(layout, module_length_m=mod["length_m"], module_width_m=mod["width_m"],
                                      module_weight_kg=mod.get("weight_kg") or st.get("module_weight_kg"),
                                      tilt_deg=spec.tilt_deg, orientation=spec.orientation,
                                      mount_height_m=spec.mount_height_m, system=st.get("system", "rcc_anchored"),
                                      spec=st)
    if req.get("bess") and energy.get("status") == "ok" and req.get("load_kw"):
        b = req["bess"]
        bess = simulate_dispatch(energy["hourly_ac_kw"], req["load_kw"], **{k: v for k, v in b.items()
                                 if k in simulate_dispatch.__code__.co_varnames})
    elif req.get("bess"):
        bess = {"status": "blocked", "reason": "BESS dispatch needs simulated PV (weather data) and an hourly load profile (load_kw)."}
    ac = None
    cab = req.get("cables", {})
    if electrical and cab.get("inverter_xy") and cab.get("acdb_xy") and "ac_kw" in electrical:
        ixy, axy = _to_true([cab["inverter_xy"]], nr)[0], _to_true([cab["acdb_xy"]], nr)[0]
        route = (abs(ixy[0] - axy[0]) + abs(ixy[1] - axy[1])) * cab.get("ac_route_factor", 1.2)
        vll = (req.get("interconnection") or {}).get("grid_voltage_v", 415)
        i_line = electrical["ac_kw"] / electrical["inverter_count"] * 1000 / (math.sqrt(3) * vll)
        sz = size_for_voltage_drop("ac", route, i_line, vll, cab.get("ac_vd_limit_pct", 1.0), min_size=4,
                                   ampacity_table=cab.get("ampacity_table"))
        ac = {"route_m": round(route, 1), "runs": electrical["inverter_count"], "current_a": round(i_line, 2), **sz}
        electrical["ac_cable"] = ac
    if structural or sld or ac or req.get("boq_rules"):
        boq = replace_allowances(boq, extra_lines(structural=structural, electrical=electrical, sld=sld, ac=ac,
                                                  rules=req.get("boq_rules"), dc_kwp=dc_kwp))
    pr = req.get("pricing", {})
    totals = price_boq(boq, pr.get("rates", {}), pr.get("tax_pct_by_category"), pr.get("wastage_pct"),
                       pr.get("freight_pct", 0), pr.get("contingency_pct", 0), dc_kwp)
    finance = None
    fin = req.get("finance")
    if fin and energy.get("status") == "ok" and totals["complete"]:
        finance = capex_model(totals["grand_total_priced_items"], energy["annual_ac_kwh"], **fin)
    elif fin:
        finance = {"status": "blocked", "reason": "Finance needs an ok energy simulation and a fully priced BOQ."}

    if not module.verified:
        warnings.append("Module record is not datasheet-verified.")
    warnings.append("All outputs are preliminary engineering estimates; qualified engineer sign-off required.")
    return {
        "engine_version": ENGINE_VERSION,
        "project": site.get("name", "Untitled"),
        "roof_true_frame": [list(map(lambda v: round(v, 4), p)) for p in roof],
        "obstacles_true_frame": obstacles,
        "layout": {k: v for k, v in layout.items() if k != "panels"},
        "panels": [{"id": p["id"], "row": p["row"], "col": p["col"],
                    "footprint": [[round(x, 4), round(y, 4)] for x, y in p["footprint"]],
                    "corners3d": [[round(v, 4) for v in c] for c in p["corners3d"]],
                    "shading_pct": shading["panel_shading_pct"][i] if shading.get("panel_shading_pct") else 0}
                   for i, p in enumerate(panels)],
        "panel_count": len(panels), "dc_kwp": dc_kwp, "ac_kw": ac_kw,
        "shading": shading, "electrical": electrical, "energy": energy,
        "boq": boq, "boq_totals": totals, "finance": finance, "warnings": warnings,
        "sld": sld, "structural": structural, "bess": bess,
    }


# ---------------- v0.2 compatibility ----------------
def legacy_to_request(s: dict) -> dict:
    """Map a v0.2 `Site` payload (saved projects) onto the v0.3 request."""
    return {
        "site": {"name": s.get("name", "Project"), "latitude": s["latitude"], "longitude": s["longitude"],
                 "roof": s["roof"], "north_rotation_deg": 0.0,
                 "obstacles": [{"name": o.get("name", "Obstacle"), "polygon": o["polygon"],
                                "height_m": o.get("height_m", 1.0)} for o in s.get("obstacles", [])]},
        "module": {"model": f"Generic {s.get('module_watts', 550)} W (unverified template)",
                   "pmax_w": s.get("module_watts", 550), "voc_v": s.get("module_voc", 49.9),
                   "vmp_v": s.get("module_vmp", 41.7), "isc_a": s.get("module_isc", 14.0),
                   "imp_a": s.get("module_watts", 550) / s.get("module_vmp", 41.7),
                   "beta_voc_pct": s.get("voc_temp_coefficient_pct", -0.28), "gamma_pmax_pct": -0.35,
                   "length_m": s.get("module_h", 2.278), "width_m": s.get("module_w", 1.134)},
        "layout": {"orientation": "portrait", "tilt_deg": s.get("tilt_deg", 0.0),
                   "array_azimuth_deg": 180 - s.get("roof_rotation_deg", 0.0),
                   "setback_m": s.get("perimeter_m", 0.5), "obstacle_clearance_m": s.get("walkway_m", 0.6),
                   "min_row_gap_m": s.get("row_gap_m", 0.25), "module_gap_m": 0.045,
                   "spacing_rule": "solstice_9_15" if s.get("tilt_deg", 0) else "fixed"},
        "shading": {"step_min": 60, "nu": 2, "nv": 2},
    }


def legacy_response(s: dict, r: dict) -> dict:
    """Shape a v0.3 result like the v0.2 /api/design response (keeps the v0.2 UI and tests working)."""
    from .electrical import string_limits
    mod = legacy_to_request(s)["module"]
    m = ModuleSpec(**{k: v for k, v in mod.items() if k in _MODULE_FIELDS})
    inv = InverterSpec(model="screen", ac_kw=1, max_dc_v=s.get("inverter_max_dc_v", 1100),
                       mppt_min_v=s.get("inverter_mppt_min_v", 200), mppt_max_v=s.get("inverter_max_dc_v", 1100),
                       mppt_count=1, strings_per_mppt=1, max_input_current_per_mppt_a=1e6, max_isc_per_mppt_a=1e6)
    lim = string_limits(m, inv, SiteTemps(s.get("minimum_design_temp_c", 5), s.get("maximum_cell_temp_c", 70)))
    n = r["panel_count"]
    nmax, nmin = lim["max_series_by_voc"], lim["min_series_by_mppt"]
    lengths = []
    if lim["series_range"] and n:
        strings = math.ceil(n / nmax)
        base, extra = divmod(n, strings)          # balanced strings instead of one short remainder
        lengths = [base + 1] * extra + [base] * (strings - extra)
    passed = bool(lengths) and all(nmin <= v <= nmax for v in lengths)
    dc = r["dc_kwp"]
    ac = round(dc / s.get("dc_ac_ratio", 1.2), 2)
    gen = round(dc * s.get("energy_yield_kwh_per_kwp", 1550))
    rates = s.get("material_rates_inr", {}) or {}
    boq = []
    for ln in r["boq"]:
        rate = rates.get(ln["item"], rates.get(ln["code"]))
        amt = round(rate * ln["qty"], 2) if rate is not None and ln["qty"] is not None else None
        boq.append({"item": ln["item"], "unit": ln["unit"], "qty": ln["qty"], "unit_rate_inr": rate,
                    "amount_inr": amt, "note": f"{ln['basis']} [{ln['confidence']} confidence]"})
    timeline = r["shading"].get("timeline", [])
    samples = [{"date": t["utc"][:10], "utc_hour": int(t["utc"][11:13]), "altitude_deg": t["elevation_deg"],
                "azimuth_deg": t["azimuth_deg"], "shaded_panels": t["panels_any_shade"],
                "shade_pct": round(100 * t["panels_any_shade"] / max(1, n), 1)} for t in timeline]
    electrical = {"module_voc": m.voc_v, "cold_voc": lim["voc_cold_v"], "hot_vmp": lim["vmp_hot_v"],
                  "max_modules_per_string_voltage": nmax, "min_modules_per_string_mppt": nmin,
                  "provisional_string_lengths": lengths, "voltage_screen_pass": passed,
                  "inverter_ac_kw": ac, "dc_ac_ratio": s.get("dc_ac_ratio", 1.2)}
    if not passed:
        electrical["warning"] = "At least one string is outside the assumed voltage window: redesign required."
    return {
        "project": s.get("name"), "engine_version": ENGINE_VERSION, "electrical": electrical,
        "priced_boq_subtotal_inr": round(sum(b["amount_inr"] or 0 for b in boq), 2),
        "roof_area_m2": r["layout"]["roof_area_m2"], "panel_count": n, "dc_kwp": dc, "indicative_ac_kw": ac,
        "annual_kwh_before_detailed_shading": gen,
        "annual_savings_inr_before_detailed_shading": round(gen * s.get("electricity_rate_inr", 9)),
        "preliminary_installed_cost_inr": round(dc * 1000 * s.get("installed_price_inr_per_w", 42)),
        "sampled_panel_shade_exposure_pct": r["shading"].get("beam_weighted_shading_pct", 0.0),
        "beam_weighted_shading_pct": r["shading"].get("beam_weighted_shading_pct", 0.0),
        "shading_samples": samples,
        "roof": r["roof_true_frame"],
        "obstacles": [{"name": o.get("name"), "polygon": [list(p) for p in o["polygon"]], "height_m": o.get("height_m")}
                      for o in r["obstacles_true_frame"]],
        "panels": [p["footprint"] for p in r["panels"]],
        "panels3d": [p["corners3d"] for p in r["panels"]],
        "parapet_height_m": s.get("parapet_height_m", 0.0),
        "panel_shading_pct": [p["shading_pct"] for p in r["panels"]],
        "boq": boq,
        "warnings": ["Layout and shading now use the v0.3 engine (3-D ray-cast, beam-weighted, all daylight hours).",
                     "Annual kWh shown here is the user-entered yield assumption; run /api/v3/design with weather data for simulated energy.",
                     *r["warnings"]],
    }
