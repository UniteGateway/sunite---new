"""Multi-roof projects: run the single-roof pipeline per roof and aggregate (Stage 3/5)."""
from __future__ import annotations
import copy
from .pipeline import run_design
from .boq import price_boq


def run_site(req: dict, weather=None) -> dict:
    roofs = req["site"].get("roofs")
    if not roofs:
        return run_design(req, weather)
    if len(roofs) > 50:
        raise ValueError("Maximum 50 roofs per project; split the site")
    results = []
    for i, roof in enumerate(roofs):
        r = copy.deepcopy(req)
        r["site"] = {**{k: v for k, v in req["site"].items() if k != "roofs"},
                     "name": f"{req['site'].get('name', 'Site')} / {roof.get('name', f'Roof {i + 1}')}",
                     "roof": roof["polygon"], "obstacles": roof.get("obstacles", []),
                     "parapet_height_m": roof.get("parapet_height_m", req["site"].get("parapet_height_m", 0.0))}
        if roof.get("layout"):
            r["layout"] = {**req.get("layout", {}), **roof["layout"]}
        r.pop("finance", None)
        results.append({"roof": roof.get("name", f"Roof {i + 1}"), "result": run_design(r, weather)})
    merged: dict[str, dict] = {}
    for rr in results:
        for ln in rr["result"]["boq"]:
            m = merged.setdefault(ln["code"], {**ln, "qty": 0, "source": "sum of roofs"})
            m["qty"] = None if (m["qty"] is None or ln["qty"] is None) else round(m["qty"] + ln["qty"], 3)
    boq = list(merged.values())
    dc = round(sum(rr["result"]["dc_kwp"] for rr in results), 3)
    pr = req.get("pricing", {})
    totals = price_boq(boq, pr.get("rates", {}), pr.get("tax_pct_by_category"), pr.get("wastage_pct"),
                       pr.get("freight_pct", 0), pr.get("contingency_pct", 0), dc)
    energies = [rr["result"]["energy"] for rr in results]
    energy = {"status": "ok", "annual_ac_kwh": round(sum(e["annual_ac_kwh"] for e in energies), 1)} \
        if all(e.get("status") == "ok" for e in energies) else {"status": energies[0].get("status", "weather_required")}
    w_sh = sum(rr["result"]["shading"].get("beam_weighted_shading_pct", 0) * rr["result"]["dc_kwp"] for rr in results)
    return {"multi_roof": True, "roofs": results, "panel_count": sum(rr["result"]["panel_count"] for rr in results),
            "dc_kwp": dc, "beam_weighted_shading_pct": round(w_sh / dc, 3) if dc else 0.0,
            "energy": energy, "boq": boq, "boq_totals": totals,
            "warnings": ["Each roof has its own string design; strings never span roofs."] +
                        sorted({w for rr in results for w in rr["result"]["warnings"]})}
