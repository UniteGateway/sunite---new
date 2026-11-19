"""Traceable bill of quantities. Every line records its source (design element,
calculation rule or declared allowance), confidence and approval status.
Rates come only from a caller-supplied rate card; missing rates stay null."""
from __future__ import annotations
import math


def _line(code, item, unit, qty, source, basis, confidence, spec="", category="material"):
    return {"code": code, "item": item, "unit": unit, "qty": qty, "specification": spec,
            "category": category, "source": source, "basis": basis, "confidence": confidence,
            "approval_status": "draft"}


def build_boq(layout: dict, electrical: dict | None, module_model: str, inverter_model: str | None,
              inverter_xy=None, dc_route_factor=1.3, string_cable_mm2=None, mounting="two_rail") -> list[dict]:
    panels = layout["panels"]
    n = len(panels)
    lines = [_line("PV-MOD", "PV modules", "Nos", n, f"layout: {n} placed modules", "count of placed modules",
                   "high", module_model)]
    if electrical and electrical.get("status") in ("pass", "fail") and "inverter_count" in electrical:
        lines.append(_line("PV-INV", "String inverters", "Nos", electrical["inverter_count"],
                           "electrical: string design", f"{electrical['string_count']} strings x "
                           f"{electrical['modules_per_string']} modules", "medium", inverter_model or ""))
        strings = electrical["string_count"]
        # MC4 pairs: one pair per string end at the inverter + spare 10% (declared allowance)
        lines.append(_line("DC-MC4", "DC connector pairs (MC4-compatible)", "Pairs",
                           strings * 2 + math.ceil(strings * 2 * 0.1), "electrical: string count",
                           "2 pairs per string + 10% spares allowance", "medium"))
        if inverter_xy is not None:
            per = electrical["modules_per_string"]
            total = 0.0
            ordered = sorted(panels, key=lambda p: (p["row"], p["col"]))
            for s in range(strings):
                grp = ordered[s * per:(s + 1) * per]
                if not grp:
                    break
                cx = sum(p["center"][0] for p in grp) / len(grp)
                cy = sum(p["center"][1] for p in grp) / len(grp)
                total += (abs(cx - inverter_xy[0]) + abs(cy - inverter_xy[1])) * dc_route_factor
            lines.append(_line("DC-CAB", "DC solar cable (+ and - conductors)", "m", round(2 * total, 1),
                               "geometry: string centroids to inverter",
                               f"rectilinear route x factor {dc_route_factor}, 2 conductors per string; "
                               "verify with measured cable route", "low",
                               f"{string_cable_mm2} mm2" if string_cable_mm2 else "size per voltage-drop calc"))
    else:
        lines.append(_line("PV-INV", "Inverters", "Nos", None, "pending", "no valid electrical design", "none"))
    if mounting == "two_rail":
        rail = sum(2 * s["length_m"] for s in layout["segments"])
        end_cl = sum(4 for _ in layout["segments"])
        mid_cl = sum(2 * (s["modules"] - 1) for s in layout["segments"])
        lines += [
            _line("MS-RAIL", "Mounting rail", "m", round(rail, 2), "layout: contiguous module segments",
                  "2 rails per segment x segment length (two-rail system rule)", "medium"),
            _line("MS-ECL", "End clamps", "Nos", end_cl, "layout segments", "4 per segment", "high"),
            _line("MS-MCL", "Mid clamps", "Nos", mid_cl, "layout segments", "2 per module joint", "high"),
        ]
    lines += [
        _line("MS-STR", "Support structure, anchors & fasteners", "Lot", None, "allowance",
              "requires certified structural design (wind/dead load)", "none"),
        _line("EL-ACDB", "AC distribution board with protection", "Lot", None, "allowance",
              "per protection design & jurisdiction ruleset", "none"),
        _line("EL-SPD", "Surge protection devices (DC & AC)", "Lot", None, "allowance",
              "per protection design", "none"),
        _line("EL-EARTH", "Earthing & lightning protection", "Lot", None, "allowance",
              "per soil resistivity test and earthing design", "none"),
        _line("SV-INST", "Installation, testing & commissioning", "kWp",
              None, "allowance", "enter per-kWp labour rate", "none",
              category="service"),
    ]
    return lines


def price_boq(lines: list[dict], rates: dict, tax_pct_by_category: dict | None = None,
              wastage_pct: dict | None = None, freight_pct: float = 0.0, contingency_pct: float = 0.0,
              dc_kwp: float | None = None) -> dict:
    tax_pct_by_category = tax_pct_by_category or {}
    wastage_pct = wastage_pct or {}
    material = service = tax = 0.0
    unpriced = []
    for ln in lines:
        if ln["code"] == "SV-INST" and ln["qty"] is None and dc_kwp:
            ln["qty"] = round(dc_kwp, 3)
        rate = rates.get(ln["code"])
        ln["unit_rate"] = rate
        if rate is None or ln["qty"] is None:
            ln["amount"] = None
            unpriced.append(ln["code"])
            continue
        q = ln["qty"] * (1 + wastage_pct.get(ln["code"], 0) / 100)
        amt = q * rate
        ln["wastage_pct"] = wastage_pct.get(ln["code"], 0)
        ln["amount"] = round(amt, 2)
        t = amt * tax_pct_by_category.get(ln["category"], 0) / 100
        ln["tax"] = round(t, 2)
        tax += t
        if ln["category"] == "service":
            service += amt
        else:
            material += amt
    freight = material * freight_pct / 100
    sub = material + service + freight + tax
    cont = sub * contingency_pct / 100
    return {"material_total": round(material, 2), "service_total": round(service, 2),
            "freight": round(freight, 2), "tax_total": round(tax, 2), "contingency": round(cont, 2),
            "grand_total_priced_items": round(sub + cont, 2), "unpriced_items": unpriced,
            "complete": not unpriced}


def extra_lines(*, structural: dict | None = None, electrical: dict | None = None, sld: dict | None = None,
                ac: dict | None = None, rules: dict | None = None, dc_kwp: float = 0.0) -> list[dict]:
    """Additional traceable lines from structural design, SLD schedule, AC routing and configurable rules.
    rules (all optional, declared in output): earth_pits_per_100kwp, la_per_1000m2, la_min, monitoring_per_inverter,
    labour_hours_per_kwp, transport_trips_per_100kwp."""
    rules = rules or {}
    out = []
    if structural:
        st = structural
        out.append(_line("MS-STEEL", "Structure steel (rafters, legs, bracing)", "kg",
                         round(sum(v for k, v in st["steel_kg_by_member"].items() if k != "rail"), 1),
                         "structural: frame geometry", f"{st['frames']} frames x member lengths x section kg/m", "medium"))
        if "anchors" in st:
            out.append(_line("MS-ANCH", "Anchor bolts / fasteners to roof", "Nos", st["anchors"]["count"],
                             "structural: frames x legs x anchors_per_leg",
                             f"design tension {st['anchors']['design_tension_per_anchor_n']} N each (prelim)", "medium"))
        if "ballast" in st:
            out.append(_line("MS-BALL", "Ballast blocks", "Nos", st["ballast"]["total_blocks"], "structural: uplift balance",
                             f"{st['ballast']['unit_kg']} kg units", "medium"))
    if sld:
        n_inv = sum(1 for r in sld["schedule"] if r["type"] == "Inverter")
        out += [_line("EL-DCSPD", "DC surge protection device", "Nos", n_inv, "SLD schedule", "1 per inverter (DCSPD-xx)", "medium"),
                _line("EL-ACB", "AC outgoing breaker per inverter", "Nos", n_inv, "SLD schedule", "ACB-xx", "medium"),
                _line("EL-ACDB1", "AC distribution board", "Nos", 1, "SLD schedule", "ACDB-01", "medium"),
                _line("EL-MTR", "Energy meter (utility approved)", "Nos", 1, "SLD schedule", "M-01", "medium")]
        if any(r["type"] == "Transformer" for r in sld["schedule"]):
            out.append(_line("EL-TX", "Transformer", "Nos", 1, "SLD schedule", "TX-01", "medium"))
    if ac and ac.get("route_m") is not None:
        out.append(_line("AC-CAB", "AC cable inverter(s) to ACDB", "m", round(ac["route_m"] * ac.get("runs", 1), 1),
                         "geometry: inverter to ACDB route", f"{ac.get('runs', 1)} run(s) x route; size {ac.get('size_mm2')} mm2",
                         "low", f"{ac.get('size_mm2')} mm2 {ac.get('cores', '4C')}"))
    if "earth_pits_per_100kwp" in rules and dc_kwp:
        out.append(_line("EL-EPIT", "Earthing pits", "Nos", max(2, math.ceil(dc_kwp / 100 * rules["earth_pits_per_100kwp"])),
                         "rule", f"{rules['earth_pits_per_100kwp']} per 100 kWp, min 2 (company rule)", "low"))
    if "monitoring_per_inverter" in rules and electrical and "inverter_count" in electrical:
        out.append(_line("MON", "Monitoring / data logger", "Nos", math.ceil(electrical["inverter_count"] * rules["monitoring_per_inverter"]),
                         "rule", f"{rules['monitoring_per_inverter']} per inverter", "medium"))
    if "labour_hours_per_kwp" in rules and dc_kwp:
        out.append(_line("SV-LAB", "Installation labour", "hours", round(dc_kwp * rules["labour_hours_per_kwp"], 1), "rule",
                         f"{rules['labour_hours_per_kwp']} h/kWp (company rule)", "low", category="service"))
    if "transport_trips_per_100kwp" in rules and dc_kwp:
        out.append(_line("SV-TRN", "Transportation", "trips", math.ceil(dc_kwp / 100 * rules["transport_trips_per_100kwp"]),
                         "rule", f"{rules['transport_trips_per_100kwp']} per 100 kWp", "low", category="service"))
    return out


def replace_allowances(lines: list[dict], extra: list[dict]) -> list[dict]:
    """Drop TBD allowance lines that are now quantified by extra lines."""
    covered = {"MS-STR": {"MS-STEEL"}, "EL-ACDB": {"EL-ACDB1"}, "EL-SPD": {"EL-DCSPD"}, "EL-EARTH": {"EL-EPIT"}}
    codes = {e["code"] for e in extra}
    keep = [l for l in lines if not (l["qty"] is None and covered.get(l["code"], set()) & codes)]
    return keep + extra
