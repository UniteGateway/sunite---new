"""Single-line diagram generator (Stage 11).

Drawn only from the approved electrical design dict (design_strings output) plus an
interconnection spec, so the drawing can never disagree with the calculation. Returns SVG,
DXF and the tagged equipment schedule; `check_consistency` verifies drawing vs design."""
from __future__ import annotations
from html import escape
from .dxfwriter import DXF


def build_sld(electrical: dict, *, project: str, module_model: str, interconnection: dict | None = None,
              bess: dict | None = None) -> dict:
    if not electrical or "allocation" not in electrical:
        raise ValueError("SLD needs a completed string design (inverter selected)")
    ic = {"system": "LT", "grid_voltage_v": 415, "metering": "net", "transformer_kva": None, "hv_kv": None, **(interconnection or {})}
    n_inv = electrical["inverter_count"]
    groups: dict = {}
    for a in electrical["allocation"]:
        groups.setdefault((a["inverter"], a["mppt"]), []).append(a["string"])
    schedule = [{"tag": f"PV-{s['string']}", "type": "PV string", "description":
                 f"{s['modules']} x {module_model} in series", "connects_to": f"INV-{s['inverter']:02d}/MPPT{s['mppt']}"}
                for s in electrical["allocation"]]
    for i in range(1, n_inv + 1):
        schedule += [{"tag": f"DCSPD-{i:02d}", "type": "DC SPD", "description": "Type 2 PV SPD (rating per design)", "connects_to": f"INV-{i:02d}"},
                     {"tag": f"INV-{i:02d}", "type": "Inverter", "description": f"{electrical['inverter']} ({electrical['ac_kw'] / n_inv:g} kW AC)", "connects_to": f"ACB-{i:02d}"},
                     {"tag": f"ACB-{i:02d}", "type": "AC breaker", "description": "Outgoing MCB/MCCB per inverter (rating per design)", "connects_to": "ACDB-01"}]
    schedule += [{"tag": "ACDB-01", "type": "AC distribution board", "description": "Busbar with AC SPD", "connects_to": "MCB-MAIN"},
                 {"tag": "MCB-MAIN", "type": "Main breaker/isolator", "description": "Main AC isolation", "connects_to": "M-01"},
                 {"tag": "M-01", "type": "Energy meter", "description": f"{ic['metering']} meter (utility approved)",
                  "connects_to": "TX-01" if ic["transformer_kva"] else "GRID"}]
    if bess:
        schedule.append({"tag": "BESS-01", "type": "Battery system", "description": f"{bess.get('energy_kwh')} kWh / {bess.get('power_kw')} kW PCS",
                         "connects_to": "ACDB-01"})
    if ic["transformer_kva"]:
        schedule.append({"tag": "TX-01", "type": "Transformer", "description": f"{ic['transformer_kva']} kVA, {ic['grid_voltage_v']} V / {ic['hv_kv']} kV", "connects_to": "GRID"})
    schedule.append({"tag": "EARTH", "type": "Earthing", "description": "PV frames, inverter and ACDB earthing; LA per lightning risk assessment", "connects_to": "-"})

    # ---- geometry (mm on an A3-like canvas, y downwards for SVG) ----
    W = 1200; row_h = 46
    rows = [(inv, mppt, strings) for (inv, mppt), strings in sorted(groups.items())]
    H = max(560, 120 + len(rows) * row_h + 120)
    svg = [f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {W} {H}" font-family="Arial" font-size="11">',
           f'<rect x="5" y="5" width="{W - 10}" height="{H - 10}" fill="white" stroke="#0B1F3F" stroke-width="2"/>',
           f'<text x="20" y="30" font-size="16" font-weight="bold" fill="#0B1F3F">SINGLE-LINE DIAGRAM: {escape(project)}</text>',
           '<text x="20" y="48" fill="#a33">PRELIMINARY - NOT FOR CONSTRUCTION. Ratings to be confirmed by a qualified electrical engineer.</text>']
    dxf = DXF()
    prims = []          # renderer-neutral primitives (y downwards), used for PDF output
    dxf.text(20, -30, 6, f"SINGLE-LINE DIAGRAM: {project}", "TEXT"); dxf.text(20, -45, 4, "PRELIMINARY - NOT FOR CONSTRUCTION", "TEXT")

    def ln(x1, y1, x2, y2, layer="CONN", dash=False):
        prims.append(("line", x1, y1, x2, y2, 1.4))
        da = ' stroke-dasharray="5,3"' if dash else ""
        svg.append(f'<line x1="{x1}" y1="{y1}" x2="{x2}" y2="{y2}" stroke="#222" stroke-width="1.4"{da}/>')
        dxf.line(x1, -y1, x2, -y2, layer)

    def box(x, y, w, h, label, sub="", layer="EQUIP", fill="#eef3fa"):
        prims.append(("rect", x, y, w, h, fill)); prims.append(("text", x + w / 2, y + h / 2 + (-3 if sub else 4), label, 11, True))
        if sub:
            prims.append(("text", x + w / 2, y + h / 2 + 11, sub, 9, False))
        svg.append(f'<rect x="{x}" y="{y}" width="{w}" height="{h}" rx="4" fill="{fill}" stroke="#0B1F3F"/>')
        svg.append(f'<text x="{x + w / 2}" y="{y + h / 2 - (3 if sub else -4)}" text-anchor="middle" font-weight="bold">{escape(label)}</text>')
        if sub:
            svg.append(f'<text x="{x + w / 2}" y="{y + h / 2 + 11}" text-anchor="middle" font-size="9">{escape(sub)}</text>')
        dxf.poly([(x, -y), (x + w, -y), (x + w, -y - h), (x, -y - h)], layer); dxf.text(x + 3, -y - h / 2, 3.5, f"{label} {sub}", "TEXT")

    y0 = 80
    inv_y: dict[int, list] = {}
    for k, (inv, mppt, strings) in enumerate(rows):
        y = y0 + k * row_h
        box(20, y, 200, 34, f"{len(strings)} string(s) x {electrical['modules_per_string']} mod",
            f"{strings[0]}..{strings[-1]}" if len(strings) > 1 else strings[0], "PV", "#e6f6ee")
        ln(220, y + 17, 330, y + 17, "DC")
        svg.append(f'<text x="226" y="{y + 12}" font-size="9">DC +/- , MPPT{mppt}</text>')
        inv_y.setdefault(inv, []).append(y + 17)
    for inv, ys in inv_y.items():
        top, bot = min(ys) - 20, max(ys) + 20
        box(330, top, 60, bot - top, "SPD", f"DCSPD-{inv:02d}", "PROT", "#fff3e0")
        ln(390, (top + bot) / 2, 450, (top + bot) / 2, "DC")
        box(450, top, 150, bot - top, f"INV-{inv:02d}", f"{electrical['ac_kw'] / n_inv:g} kW AC", "EQUIP")
        ln(600, (top + bot) / 2, 680, (top + bot) / 2, "AC")
        box(680, (top + bot) / 2 - 15, 70, 30, f"ACB-{inv:02d}", "", "PROT", "#fff3e0")
        ln(750, (top + bot) / 2, 800, (top + bot) / 2, "AC")
    bus_top, bus_bot = y0 - 10, y0 + len(rows) * row_h
    prims.append(("line", 800, bus_top, 800, bus_bot, 5)); prims.append(("ltext", 806, bus_top + 4, f"ACDB-01 ({ic['grid_voltage_v']} V)", 11, True))
    svg.append(f'<line x1="800" y1="{bus_top}" x2="800" y2="{bus_bot}" stroke="#0B1F3F" stroke-width="5"/>')
    dxf.line(800, -bus_top, 800, -bus_bot, "BUSBAR")
    svg.append(f'<text x="806" y="{bus_top + 4}" font-weight="bold">ACDB-01 ({ic["grid_voltage_v"]} V)</text>')
    mid = (bus_top + bus_bot) / 2
    ln(800, mid, 860, mid, "AC"); box(860, mid - 18, 80, 36, "MCB-MAIN", "isolator", "PROT", "#fff3e0")
    ln(940, mid, 980, mid, "AC"); box(980, mid - 18, 70, 36, "M-01", ic["metering"], "EQUIP")
    x = 1050
    if ic["transformer_kva"]:
        ln(x, mid, x + 15, mid, "AC")
        svg.append(f'<circle cx="{x + 30}" cy="{mid}" r="14" fill="none" stroke="#222"/><circle cx="{x + 48}" cy="{mid}" r="14" fill="none" stroke="#222"/>')
        dxf.circle(x + 30, -mid, 14, "EQUIP"); dxf.circle(x + 48, -mid, 14, "EQUIP")
        prims += [("circle", x + 30, mid, 14), ("circle", x + 48, mid, 14), ("text", x + 39, mid + 30, f"TX-01 {ic['transformer_kva']} kVA", 9, False)]
        svg.append(f'<text x="{x + 39}" y="{mid + 30}" text-anchor="middle" font-size="9">TX-01 {ic["transformer_kva"]} kVA</text>')
        x += 62
    ln(x, mid, W - 30, mid, "AC")
    svg.append(f'<text x="{W - 30}" y="{mid - 8}" text-anchor="end" font-weight="bold">GRID</text>')
    prims.append(("rtext", W - 30, mid - 8, "GRID", 11, True))
    if bess:
        box(860, bus_bot + 30, 140, 40, "BESS-01", f"{bess.get('energy_kwh')} kWh / {bess.get('power_kw')} kW", "EQUIP", "#ede7f6")
        ln(800, bus_bot + 50, 860, bus_bot + 50, "AC")
    ey = H - 40
    svg.append(f'<text x="20" y="{ey}" font-size="10">Earthing: all module frames, structures, inverters, ACDB bonded to earth; lightning protection per risk assessment. '
               f'Strings: {electrical["string_count"]} · Inverters: {n_inv} · DC/AC {electrical["dc_ac_ratio"]}</text>')
    svg.append("</svg>")
    prims.append(("ltext", 20, ey, f"Strings: {electrical['string_count']} | Inverters: {n_inv} | DC/AC {electrical['dc_ac_ratio']} | "
                  "Earthing: frames, structures, inverters and ACDB bonded; LA per risk assessment", 9, False))
    for k, (inv, mppt, strings) in enumerate(rows):
        prims.append(("ltext", 226, y0 + k * row_h + 12, f"DC, MPPT{mppt}", 8, False))
    return {"svg": "".join(svg), "dxf": dxf.render(insunits=4), "schedule": schedule, "primitives": prims, "canvas": [W, H],
            "string_count_drawn": sum(len(s) for _, _, s in rows), "inverters_drawn": len(inv_y)}


def check_consistency(sld: dict, electrical: dict) -> list[str]:
    issues = []
    if sld["string_count_drawn"] != electrical["string_count"]:
        issues.append(f"SLD shows {sld['string_count_drawn']} strings, design has {electrical['string_count']}")
    if sld["inverters_drawn"] != electrical["inverter_count"]:
        issues.append(f"SLD shows {sld['inverters_drawn']} inverters, design has {electrical['inverter_count']}")
    tags = [r["tag"] for r in sld["schedule"]]
    if len(tags) != len(set(tags)):
        issues.append("Duplicate equipment tags")
    return issues
