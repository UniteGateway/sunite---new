"""Preliminary mounting-structure quantities and loads (Stage 12).

Geometry comes from the placed layout (contiguous row segments). Section weights, wind-code
factors, pressure coefficients, anchor counts and safety factors are ALL caller inputs, recorded
in the output; no code tables are hard-coded. Output is never a structural approval."""
from __future__ import annotations
import math

G = 9.80665
SYSTEMS = ("rcc_ballast", "rcc_anchored", "metal_roof_rail", "elevated", "ground")


def design_structure(layout: dict, *, module_length_m, module_width_m, module_weight_kg, tilt_deg,
                     orientation="portrait", mount_height_m=0.3, system="rcc_anchored", spec: dict) -> dict:
    """spec keys: max_support_spacing_m, sections_kg_per_m{rail,leg,rafter,brace}, anchors_per_leg,
    wind{vb_ms,k1,k2,k3,k4,kd,ka,kc,cp_net_uplift,cp_net_down}, uplift_safety_factor,
    roof_capacity_kn_m2 (optional), ballast_unit_kg (rcc_ballast)."""
    if system not in SYSTEMS:
        raise ValueError(f"system must be one of {SYSTEMS}")
    need = ["max_support_spacing_m", "sections_kg_per_m", "wind", "uplift_safety_factor"]
    miss = [k for k in need if k not in spec]
    if miss:
        raise ValueError(f"structural spec missing {miss}")
    sec = spec["sections_kg_per_m"]
    for k in ("rail", "rafter", "leg"):
        if k not in sec:
            raise ValueError(f"sections_kg_per_m.{k} required (from the chosen section catalogue)")
    slope = module_length_m if orientation == "portrait" else module_width_m
    t = math.radians(tilt_deg)
    depth, rise = slope * math.cos(t), slope * math.sin(t)
    h_lo, h_hi = mount_height_m, mount_height_m + rise
    span = spec["max_support_spacing_m"]
    frames = members_len = 0.0
    lengths = {"rail": 0.0, "rafter": 0.0, "front_leg": 0.0, "back_leg": 0.0, "brace": 0.0}
    seg_rows = []
    for s in layout["segments"]:
        L = s["length_m"]
        n_frames = math.ceil(L / span) + 1
        frames += n_frames
        lengths["rail"] += 2 * L
        lengths["rafter"] += n_frames * slope
        lengths["front_leg"] += n_frames * h_lo
        lengths["back_leg"] += n_frames * h_hi
        if "brace" in sec and tilt_deg > 0:
            lengths["brace"] += n_frames * math.hypot(depth, h_hi)
        seg_rows.append({"row": s["row"], "modules": s["modules"], "length_m": L, "frames": n_frames,
                         "actual_spacing_m": round(L / (n_frames - 1), 3)})
    kg = {"rail": lengths["rail"] * sec["rail"], "rafter": lengths["rafter"] * sec["rafter"],
          "legs": (lengths["front_leg"] + lengths["back_leg"]) * sec["leg"],
          "brace": lengths["brace"] * sec.get("brace", 0.0)}
    steel_kg = sum(kg.values())
    n_mod = sum(s["modules"] for s in layout["segments"])
    footprint = sum(s["length_m"] for s in layout["segments"]) * depth
    w = spec["wind"]
    vz = w["vb_ms"] * w.get("k1", 1) * w.get("k2", 1) * w.get("k3", 1) * w.get("k4", 1)
    pz = 0.6 * vz ** 2                                      # N/m2 (IS 875-3 form, inputs per site)
    pd = pz * w.get("kd", 1) * w.get("ka", 1) * w.get("kc", 1)
    trib = span * slope                                     # m2 of module per interior frame
    uplift_n = pd * abs(w["cp_net_uplift"]) * trib
    down_n = pd * abs(w.get("cp_net_down", 0)) * trib
    dead_frame_n = (module_weight_kg * trib / (module_length_m * module_width_m) + steel_kg / max(frames, 1)) * G
    net_uplift = uplift_n - dead_frame_n
    sf = spec["uplift_safety_factor"]
    out = {"system": system, "frames": int(frames), "segments": seg_rows,
           "member_lengths_m": {k: round(v, 2) for k, v in lengths.items()},
           "steel_kg_by_member": {k: round(v, 1) for k, v in kg.items()}, "steel_tonnes": round(steel_kg / 1000, 3),
           "loads": {"vz_ms": round(vz, 2), "pz_n_m2": round(pz, 1), "pd_n_m2": round(pd, 1),
                     "tributary_area_m2": round(trib, 3), "uplift_per_frame_n": round(uplift_n, 1),
                     "downward_wind_per_frame_n": round(down_n, 1), "dead_load_per_frame_n": round(dead_frame_n, 1),
                     "net_uplift_per_frame_n": round(net_uplift, 1)},
           "inputs": {"spec": spec, "tilt_deg": tilt_deg, "module_weight_kg": module_weight_kg}, "flags": []}
    ballast_kg = 0.0
    if system == "rcc_ballast":
        need_kg = max(0.0, net_uplift) * sf / G
        unit = spec.get("ballast_unit_kg")
        if not unit:
            raise ValueError("ballast_unit_kg required for ballasted systems")
        blocks = math.ceil(need_kg / unit) if need_kg > 0 else 0
        ballast_kg = blocks * unit * frames
        out["ballast"] = {"per_frame_kg_required": round(need_kg, 1), "blocks_per_frame": blocks,
                          "unit_kg": unit, "total_blocks": int(blocks * frames), "total_kg": round(ballast_kg, 1)}
    else:
        apl = spec.get("anchors_per_leg")
        if not apl:
            raise ValueError("anchors_per_leg required for anchored systems")
        anchors = int(frames * 2 * apl)
        out["anchors"] = {"count": anchors,
                          "design_tension_per_anchor_n": round(max(0.0, net_uplift) * sf / (2 * apl), 1),
                          "note": "Select anchor whose certified tension capacity exceeds this value in the actual concrete/purlin"}
    total_kg = n_mod * module_weight_kg + steel_kg + ballast_kg
    q = total_kg * G / 1000 / footprint if footprint else 0
    out["roof_loading"] = {"total_added_mass_kg": round(total_kg, 1), "array_footprint_m2": round(footprint, 2),
                           "uniform_dead_load_kn_m2": round(q, 3)}
    cap = spec.get("roof_capacity_kn_m2")
    if cap is not None and q > cap:
        out["flags"].append(f"Added dead load {q:.2f} kN/m2 exceeds stated roof capacity {cap} kN/m2")
    if cap is None:
        out["flags"].append("Roof load capacity not provided: structural engineer must confirm the roof can carry the added load")
    if any(r["actual_spacing_m"] > span + 1e-9 for r in seg_rows):
        out["flags"].append("Support spacing exceeds maximum")
    out["flags"].append("PRELIMINARY: member sizing, connections, anchorage and roof capacity require a certified structural design")
    out["approval"] = "NOT APPROVED - for quantity estimation only"
    return out
