"""Excel BOQ and PDF design report generation (openpyxl, reportlab, matplotlib)."""
from __future__ import annotations
import io, os
from datetime import datetime, timezone

NAVY, ORANGE = "0B1F3F", "F28C0F"
LOGO = os.path.join(os.path.dirname(__file__), "..", "..", "frontend", "assets", "sunite-logo-small.png")


def boq_xlsx(result: dict) -> bytes:
    from openpyxl import Workbook
    from openpyxl.styles import Font, PatternFill, Alignment
    wb = Workbook()
    ws = wb.active
    ws.title = "BOQ"
    ws.append(["SUNITE AI SOLAR DESIGNER - BILL OF QUANTITIES (PRELIMINARY)"])
    ws["A1"].font = Font(bold=True, size=14, color=NAVY)
    ws.append(["Project", result["project"]])
    ws.append(["Engine version", result["engine_version"]])
    ws.append(["DC capacity (kWp)", result["dc_kwp"]])
    ws.append(["Generated (UTC)", datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M")])
    ws.append(["Status", "DRAFT - quantities require engineering approval"])
    ws.append([])
    head = ["Code", "Item", "Specification", "Unit", "Qty", "Unit rate", "Wastage %", "Amount", "Tax",
            "Source", "Calculation basis", "Confidence", "Approval"]
    ws.append(head)
    hr = ws.max_row
    for c in ws[hr]:
        c.font = Font(bold=True, color="FFFFFF")
        c.fill = PatternFill("solid", fgColor=NAVY)
        c.alignment = Alignment(wrap_text=True, vertical="center")
    for l in result["boq"]:
        ws.append([l["code"], l["item"], l.get("specification", ""), l["unit"], l["qty"], l.get("unit_rate"),
                   l.get("wastage_pct"), l.get("amount"), l.get("tax"), l["source"], l["basis"], l["confidence"],
                   l["approval_status"]])
    t = result["boq_totals"]
    ws.append([])
    for k in ("material_total", "service_total", "freight", "tax_total", "contingency", "grand_total_priced_items"):
        ws.append(["", k.replace("_", " ").title(), "", "", "", "", "", t[k]])
        ws.cell(ws.max_row, 2).font = Font(bold=k.startswith("grand"))
    if t["unpriced_items"]:
        ws.append(["", "Unpriced items (excluded from totals)", ", ".join(t["unpriced_items"])])
        ws.cell(ws.max_row, 2).font = Font(bold=True, color="B00020")
    widths = [10, 38, 22, 7, 10, 11, 9, 13, 11, 32, 48, 11, 10]
    for i, w in enumerate(widths):
        ws.column_dimensions[chr(65 + i)].width = w
    ws.freeze_panes = ws.cell(hr + 1, 1)
    if result.get("electrical") and result["electrical"].get("checks"):
        e = wb.create_sheet("Electrical checks")
        e.append(["Check", "Value", "Operator", "Limit", "Unit", "Result"])
        for c in result["electrical"]["checks"]:
            e.append([c["check"], c["value"], c["operator"], c["limit"], c["unit"], "PASS" if c["pass"] else "FAIL"])
        e.append([]); e.append(["Ruleset", result["electrical"]["ruleset"]])
        e.column_dimensions["A"].width = 38; e.column_dimensions["B"].width = 50
    s = wb.create_sheet("Panel shading")
    s.append(["Module", "Row", "Col", "Beam-weighted shading %"])
    for p in result["panels"]:
        s.append([p["id"], p["row"], p["col"], p["shading_pct"]])
    buf = io.BytesIO(); wb.save(buf)
    return buf.getvalue()


def _layout_png(result: dict) -> bytes:
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    from matplotlib.patches import Polygon as MPoly
    from matplotlib import cm, colors
    fig, ax = plt.subplots(figsize=(7.2, 4.6), dpi=150)
    roof = result["roof_true_frame"]
    ax.add_patch(MPoly(roof, closed=True, fill=True, fc="#eef2f7", ec="#0B1F3F", lw=1.4))
    vals = [p["shading_pct"] for p in result["panels"]]
    vmax = max(5.0, max(vals) if vals else 0)
    norm = colors.Normalize(0, vmax)
    cmap = matplotlib.colormaps["YlOrRd"]
    for p in result["panels"]:
        ax.add_patch(MPoly(p["footprint"], closed=True, fc=cmap(norm(p["shading_pct"])), ec="#1d3557", lw=0.3))
    for o in result["obstacles_true_frame"]:
        pts = [tuple(q) for q in o["polygon"]]
        ax.add_patch(MPoly(pts, closed=True, fc="#5c677d", ec="black", lw=0.6, hatch="//"))
        cx = sum(x for x, _ in pts) / len(pts); cy = sum(y for _, y in pts) / len(pts)
        ax.text(cx, cy, f"{o.get('name','')}\n{o.get('height_m','')} m", ha="center", va="center", fontsize=6, color="white")
    xs, ys = zip(*roof)
    ax.set_xlim(min(xs) - 1, max(xs) + 1); ax.set_ylim(min(ys) - 1, max(ys) + 1)
    ax.set_aspect("equal"); ax.set_xlabel("East (m)"); ax.set_ylabel("North (m)")
    ax.annotate("N", xy=(1.02, 1.0), xytext=(1.02, 0.88), xycoords="axes fraction", ha="center",
                arrowprops=dict(arrowstyle="-|>", color="#F28C0F"), color="#F28C0F", fontweight="bold", annotation_clip=False)
    sm = cm.ScalarMappable(norm=norm, cmap=cmap); sm.set_array([])
    fig.colorbar(sm, ax=ax, fraction=0.03, pad=0.02, label="Beam-weighted shading %")
    fig.tight_layout()
    buf = io.BytesIO(); fig.savefig(buf, format="png"); plt.close(fig)
    return buf.getvalue()


def design_report_pdf(result: dict) -> bytes:
    from reportlab.lib import colors
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
    from reportlab.lib.units import mm
    from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle, Image, PageBreak
    navy, orange = colors.HexColor("#" + NAVY), colors.HexColor("#" + ORANGE)
    ss = getSampleStyleSheet()
    h1 = ParagraphStyle("h1", parent=ss["Heading1"], textColor=navy, fontSize=16, spaceAfter=6)
    h2 = ParagraphStyle("h2", parent=ss["Heading2"], textColor=navy, fontSize=12, spaceBefore=10, spaceAfter=4)
    body = ParagraphStyle("b", parent=ss["BodyText"], fontSize=8.5, leading=11)
    small = ParagraphStyle("s", parent=body, fontSize=7, textColor=colors.HexColor("#555555"))

    def tbl(rows, widths, head=True):
        hdr = ParagraphStyle("hd", parent=body, textColor=colors.white, fontName="Helvetica-Bold")
        t = Table([[Paragraph(str(c), hdr if (head and i == 0) else body) for c in r] for i, r in enumerate(rows)],
                  colWidths=widths, repeatRows=1 if head else 0)
        st = [("GRID", (0, 0), (-1, -1), 0.25, colors.HexColor("#c9d3e0")), ("VALIGN", (0, 0), (-1, -1), "TOP")]
        if head:
            st += [("BACKGROUND", (0, 0), (-1, 0), navy), ("TEXTCOLOR", (0, 0), (-1, 0), colors.white)]
        t.setStyle(TableStyle(st))
        return t

    def frame(canvas, doc):
        canvas.saveState()
        canvas.setFillColor(orange); canvas.rect(15 * mm, 287 * mm, 180 * mm, 0.8 * mm, fill=1, stroke=0)
        canvas.setFont("Helvetica", 7); canvas.setFillColor(colors.grey)
        canvas.drawString(15 * mm, 10 * mm, "SUNITE AI Solar Designer - preliminary engineering output; "
                                            "requires review and sign-off by qualified engineers.")
        canvas.drawRightString(195 * mm, 10 * mm, f"Page {doc.page}")
        canvas.restoreState()

    buf = io.BytesIO()
    doc = SimpleDocTemplate(buf, pagesize=A4, leftMargin=15 * mm, rightMargin=15 * mm, topMargin=14 * mm,
                            bottomMargin=16 * mm, title=f"Solar design report - {result['project']}")
    el = []
    if os.path.exists(LOGO):
        el.append(Image(LOGO, width=55 * mm, height=27 * mm, hAlign="LEFT"))
    el += [Paragraph(f"Rooftop Solar Design Report: {result['project']}", h1),
           Paragraph(f"Engine {result['engine_version']} | generated "
                     f"{datetime.now(timezone.utc).strftime('%d %b %Y %H:%M UTC')} | status: DRAFT", small)]
    lay, sh, el_, en = result["layout"], result["shading"], result.get("electrical"), result["energy"]
    summary = [["Parameter", "Value"],
               ["Roof area", f"{lay['roof_area_m2']} sq.m"], ["Modules placed", result["panel_count"]],
               ["DC capacity", f"{result['dc_kwp']} kWp"],
               ["AC capacity", f"{result['ac_kw']} kW" if result.get("ac_kw") else "pending inverter selection"],
               ["Rows / row pitch", f"{lay['rows']} rows / {lay['row_pitch_m']} m (gap {lay['row_gap_m']} m)"],
               ["Row-gap basis", lay["row_gap_basis"].get("rule", "")],
               ["Beam-weighted shading", f"{sh.get('beam_weighted_shading_pct', 0)} %"],
               ["Annual AC energy", f"{en['annual_ac_kwh']:,.0f} kWh ({en['weather_source']})" if en.get("status") == "ok"
                else "Not simulated - weather data required (no figure fabricated)"]]
    if en.get("status") == "ok":
        summary += [["Specific yield / PR", f"{en['specific_yield_kwh_per_kwp']} kWh/kWp / {en['performance_ratio']}"]]
    el += [Paragraph("1. Summary", h2), tbl(summary, [55 * mm, 125 * mm])]
    el += [Paragraph("2. Module layout and shading heat-map", h2), Image(io.BytesIO(_layout_png(result)), width=165 * mm, height=105 * mm)]
    el += [Paragraph(f"Method: {sh.get('method', '')}. " + " ".join(sh.get("limitations", [])), small)]
    if sh.get("monthly_beam_shading_pct"):
        months = "Jan Feb Mar Apr May Jun Jul Aug Sep Oct Nov Dec".split()
        m = sh["monthly_beam_shading_pct"]
        el += [Spacer(1, 4), tbl([months, [m.get(str(i + 1), "-") for i in range(12)]], [15 * mm] * 12)]
    el.append(Spacer(1, 6))
    el.append(Paragraph("3. Electrical string design", h2))
    if el_ and el_.get("checks"):
        el += [Paragraph(f"{el_['inverter_count']} x {el_['inverter']} | {el_['string_count']} strings x "
                         f"{el_['modules_per_string']} modules | DC/AC {el_['dc_ac_ratio']} | overall: "
                         f"<b>{el_['status'].upper()}</b>", body), Spacer(1, 3),
               tbl([["Check", "Value", "Limit", "Result"]] +
                   [[c["check"], f"{c['value']} {c['unit']}", f"{c['operator']} {c['limit']} {c['unit']}",
                     "PASS" if c["pass"] else "FAIL"] for c in el_["checks"]], [75 * mm, 35 * mm, 40 * mm, 30 * mm]),
               Paragraph(f"Ruleset: {el_['ruleset']}. {el_['approval']}", small)]
        if el_.get("dc_cable"):
            dcab = el_["dc_cable"]
            el.append(Paragraph(f"Longest DC route {dcab.get('longest_route_m')} m: {dcab.get('size_mm2')} mm2 gives "
                                f"{dcab.get('voltage_drop_pct')} % drop (limit {dcab.get('limit_pct')} %). "
                                f"Ampacity: {dcab.get('ampacity_check')}.", body))
        if result.get("sld"):
            el += [Paragraph("Single-line diagram (preliminary)", h2), sld_drawing(result["sld"], 180 * mm)]
            if result["sld"].get("consistency_issues"):
                el.append(Paragraph("SLD consistency issues: " + "; ".join(result["sld"]["consistency_issues"]), body))
    elif el_:
        el.append(Paragraph(f"FAILED: {el_.get('error')}", body))
    else:
        el.append(Paragraph("Pending: no inverter selected.", body))
    st = result.get("structural")
    if st:
        el.append(Paragraph("Structure (preliminary quantities and loads)", h2))
        L = st["loads"]
        el.append(tbl([["Parameter", "Value"], ["System / frames", f"{st['system']} / {st['frames']}"],
                       ["Steel", f"{st['steel_tonnes']} t"], ["Design wind pressure pd", f"{L['pd_n_m2']} N/m2"],
                       ["Net uplift per frame", f"{L['net_uplift_per_frame_n']} N"],
                       ["Added roof dead load", f"{st['roof_loading']['uniform_dead_load_kn_m2']} kN/m2"]]
                      + ([["Anchors", f"{st['anchors']['count']} (design tension {st['anchors']['design_tension_per_anchor_n']} N)"]] if "anchors" in st else [])
                      + ([["Ballast", f"{st['ballast']['total_blocks']} x {st['ballast']['unit_kg']} kg"]] if "ballast" in st else []),
                      [70 * mm, 110 * mm]))
        el.append(Paragraph(" ".join(st["flags"]), small))
    bs = result.get("bess")
    if bs and "totals_kwh" in bs:
        el.append(Paragraph("Battery dispatch", h2))
        el.append(tbl([["Indicator", "Value"], ["Strategy", bs["strategy"]], ["Self-sufficiency", f"{bs['self_sufficiency_pct']} %"],
                       ["Self-consumption", f"{bs['self_consumption_pct']} %"], ["Equivalent full cycles", bs["equivalent_full_cycles"]],
                       ["Energy balance error", f"{bs['energy_balance_error_kwh']} kWh"]], [70 * mm, 110 * mm]))
    el.append(Paragraph("4. Bill of quantities", h2))
    el.append(tbl([["Code", "Item", "Qty", "Unit", "Basis", "Conf."]] +
                  [[l["code"], l["item"], "TBD" if l["qty"] is None else l["qty"], l["unit"], l["basis"], l["confidence"]]
                   for l in result["boq"]], [18 * mm, 45 * mm, 15 * mm, 12 * mm, 75 * mm, 15 * mm]))
    if result["boq_totals"]["unpriced_items"]:
        el.append(Paragraph("Unpriced (excluded from totals): " + ", ".join(result["boq_totals"]["unpriced_items"]), small))
    el.append(Paragraph("5. Warnings and limitations", h2))
    for w in result["warnings"]:
        el.append(Paragraph("• " + w, body))
    doc.build(el, onFirstPage=frame, onLaterPages=frame)
    return buf.getvalue()


def sld_drawing(sld: dict, width_pt: float):
    """reportlab Drawing from SLD primitives (same geometry as the SVG/DXF)."""
    from reportlab.graphics.shapes import Drawing, Line, Rect, String, Circle
    from reportlab.lib import colors
    W, H = sld["canvas"]
    k = width_pt / W
    d = Drawing(width_pt, H * k)
    Y = lambda y: (H - y) * k
    for pr in sld["primitives"]:
        t = pr[0]
        if t == "line":
            d.add(Line(pr[1] * k, Y(pr[2]), pr[3] * k, Y(pr[4]), strokeWidth=pr[5] * k * 0.9, strokeColor=colors.HexColor("#222222")))
        elif t == "rect":
            d.add(Rect(pr[1] * k, Y(pr[2] + pr[4]), pr[3] * k, pr[4] * k, fillColor=colors.HexColor(pr[5]),
                       strokeColor=colors.HexColor("#0B1F3F"), strokeWidth=0.6, rx=3 * k, ry=3 * k))
        elif t == "circle":
            d.add(Circle(pr[1] * k, Y(pr[2]), pr[3] * k, fillColor=None, strokeColor=colors.black, strokeWidth=0.6))
        else:
            anchor = {"text": "middle", "ltext": "start", "rtext": "end"}[t]
            d.add(String(pr[1] * k, Y(pr[2]), str(pr[3])[:90], fontSize=pr[4] * k, textAnchor=anchor,
                         fontName="Helvetica-Bold" if pr[5] else "Helvetica"))
    return d


def proposal_pdf(result: dict, *, customer: dict, company: dict, commercial: dict, quotation: dict) -> bytes:
    """Customer-facing proposal. commercial = output of a finance model (+ 'model' name & key inputs);
    quotation = {number, version, total, currency, valid_until, inclusions[], exclusions[], payment_terms[]}.
    Every figure is taken from stored results; nothing is computed here except formatting."""
    from reportlab.lib import colors
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
    from reportlab.lib.units import mm
    from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle, Image, PageBreak
    navy, orange = colors.HexColor("#" + NAVY), colors.HexColor("#" + ORANGE)
    ss = getSampleStyleSheet()
    h1 = ParagraphStyle("h1", parent=ss["Heading1"], textColor=navy, fontSize=20, leading=24)
    h2 = ParagraphStyle("h2", parent=ss["Heading2"], textColor=navy, fontSize=13, spaceBefore=10)
    body = ParagraphStyle("b", parent=ss["BodyText"], fontSize=9.5, leading=13)
    small = ParagraphStyle("s", parent=body, fontSize=7.5, textColor=colors.HexColor("#555555"))
    cur = quotation.get("currency", "INR")
    money = lambda v: "-" if v is None else f"{cur} {v:,.0f}"

    def tbl(rows, widths):
        hdr = ParagraphStyle("hd", parent=body, textColor=colors.white, fontName="Helvetica-Bold")
        t = Table([[Paragraph(str(c), hdr if i == 0 else body) for c in r] for i, r in enumerate(rows)], colWidths=widths)
        t.setStyle(TableStyle([("BACKGROUND", (0, 0), (-1, 0), navy), ("GRID", (0, 0), (-1, -1), 0.25, colors.HexColor("#c9d3e0")),
                               ("VALIGN", (0, 0), (-1, -1), "TOP")]))
        return t

    def frame(c, doc):
        c.saveState(); c.setFillColor(orange); c.rect(15 * mm, 287 * mm, 180 * mm, 0.8 * mm, fill=1, stroke=0)
        c.setFont("Helvetica", 7); c.setFillColor(colors.grey)
        c.drawString(15 * mm, 10 * mm, f"{company.get('name', '')} | {company.get('contact', '')}")
        c.drawRightString(195 * mm, 10 * mm, f"Proposal {quotation.get('number', '')} v{quotation.get('version', 1)} | Page {doc.page}")
        c.restoreState()

    buf = io.BytesIO()
    doc = SimpleDocTemplate(buf, pagesize=A4, leftMargin=15 * mm, rightMargin=15 * mm, topMargin=14 * mm, bottomMargin=16 * mm,
                            title=f"Solar proposal - {customer.get('name', '')}")
    el = []
    if os.path.exists(LOGO):
        el.append(Image(LOGO, width=60 * mm, height=30 * mm, hAlign="LEFT"))
    el += [Spacer(1, 6), Paragraph("Rooftop Solar Proposal", h1),
           Paragraph(f"Prepared for <b>{customer.get('name', '')}</b>, {customer.get('address', '')}", body),
           Paragraph(f"Proposal {quotation.get('number', '')} · version {quotation.get('version', 1)} · valid until "
                     f"{quotation.get('valid_until') or 'as stated'}", small), Spacer(1, 8)]
    en = result.get("energy", {})
    sysrows = [["Item", "Value"], ["System size", f"{result['dc_kwp']} kWp DC"
               + (f" / {result['ac_kw']} kW AC" if result.get("ac_kw") else "")],
               ["Modules", f"{result['panel_count']} x {result.get('electrical', {}).get('module', '') if result.get('electrical') else ''}"],
               ["Annual generation (year 1)", f"{en['annual_ac_kwh']:,.0f} kWh ({en.get('weather_source')})" if en.get("status") == "ok"
                else "To be confirmed after site-specific simulation"],
               ["Shading (beam-weighted)", f"{result['shading'].get('beam_weighted_shading_pct', 0)} %"]]
    el += [Paragraph("1. Proposed system", h2), tbl(sysrows, [60 * mm, 120 * mm])]
    el += [Paragraph("2. Layout", h2), Image(io.BytesIO(_layout_png(result)), width=160 * mm, height=102 * mm)]
    el.append(PageBreak())
    model = commercial.get("model", "capex")
    el.append(Paragraph(f"3. Commercial offer ({model.upper()})", h2))
    el.append(tbl([["Description", "Amount"], ["Contract price", money(quotation.get("total"))]] +
                  ([["Monthly EMI", money(commercial.get("monthly_emi"))]] if commercial.get("monthly_emi") else []),
                  [100 * mm, 80 * mm]))
    flows = commercial.get("cashflows") or commercial.get("customer_cashflows")
    if flows:
        cum, rows = 0.0, [["Year", "Net benefit", "Cumulative"]]
        for y, f in enumerate(flows):
            cum += f
            if y <= 10 or y % 5 == 0 or y == len(flows) - 1:
                rows.append([str(y), money(f), money(cum)])
        el += [Spacer(1, 6), Paragraph("Projected cash flows (customer view)", body), tbl(rows, [25 * mm, 70 * mm, 70 * mm])]
        keys = [("irr_pct", "IRR %"), ("npv", "NPV"), ("simple_payback_years", "Payback (years)"),
                ("customer_savings_npv", "Savings NPV"), ("customer_total_savings", "Total savings")]
        kv = [[lbl, (money(commercial[k]) if k in ("npv", "customer_savings_npv", "customer_total_savings") else commercial[k])]
              for k, lbl in keys if commercial.get(k) is not None]
        if kv:
            el += [Spacer(1, 6), tbl([["Indicator", "Value"]] + kv, [100 * mm, 80 * mm])]
    if commercial.get("assumptions"):
        el.append(Paragraph("Assumptions: " + "; ".join(f"{k}: {v}" for k, v in commercial["assumptions"].items()), small))
    for title, key in (("Inclusions", "inclusions"), ("Exclusions", "exclusions"), ("Payment terms", "payment_terms")):
        if quotation.get(key):
            el.append(Paragraph(title, h2))
            for x in quotation[key]:
                el.append(Paragraph("• " + str(x), body))
    el += [Spacer(1, 10), Paragraph(
        "Estimates depend on weather, site conditions, tariffs and regulations in force. Tariff, subsidy and tax figures "
        "are as stated in the assumptions and must be confirmed at contract. Final design is subject to site survey and "
        "engineering approval.", small)]
    doc.build(el, onFirstPage=frame, onLaterPages=frame)
    return buf.getvalue()


# ---------------------------------------------------------------- Stage 20: further report types
def _simple_pdf(title: str, subtitle: str, sections: list[tuple[str, list]], footer: str) -> bytes:
    """sections: [(heading, [ ('table', rows, widths_mm) | ('text', str) ])]"""
    from reportlab.lib import colors
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
    from reportlab.lib.units import mm
    from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle, Image
    navy, orange = colors.HexColor("#" + NAVY), colors.HexColor("#" + ORANGE)
    ss = getSampleStyleSheet()
    h1 = ParagraphStyle("h1", parent=ss["Heading1"], textColor=navy, fontSize=16)
    h2 = ParagraphStyle("h2", parent=ss["Heading2"], textColor=navy, fontSize=12, spaceBefore=8)
    body = ParagraphStyle("b", parent=ss["BodyText"], fontSize=8.5, leading=11)
    hdr = ParagraphStyle("hd", parent=body, textColor=colors.white, fontName="Helvetica-Bold")

    def frame(c, doc):
        c.saveState(); c.setFillColor(orange); c.rect(15 * mm, 287 * mm, 180 * mm, 0.8 * mm, fill=1, stroke=0)
        c.setFont("Helvetica", 7); c.setFillColor(colors.grey); c.drawString(15 * mm, 10 * mm, footer[:150])
        c.drawRightString(195 * mm, 10 * mm, f"Page {doc.page}"); c.restoreState()
    buf = io.BytesIO()
    doc = SimpleDocTemplate(buf, pagesize=A4, leftMargin=15 * mm, rightMargin=15 * mm, topMargin=14 * mm, bottomMargin=16 * mm, title=title)
    el = [Image(LOGO, width=45 * mm, height=22 * mm, hAlign="LEFT")] if os.path.exists(LOGO) else []
    el += [Paragraph(title, h1), Paragraph(subtitle, body), Spacer(1, 6)]
    for head, blocks in sections:
        el.append(Paragraph(head, h2))
        for b in blocks:
            if b[0] == "text":
                el.append(Paragraph(b[1], body))
            elif b[0] == "flow":
                el.append(b[1])
            else:
                rows, widths = b[1], b[2]
                t = Table([[Paragraph(str(c), hdr if i == 0 else body) for c in r] for i, r in enumerate(rows)],
                          colWidths=[w * mm for w in widths], repeatRows=1)
                t.setStyle(TableStyle([("BACKGROUND", (0, 0), (-1, 0), navy), ("GRID", (0, 0), (-1, -1), 0.25, colors.HexColor("#c9d3e0")),
                                       ("VALIGN", (0, 0), (-1, -1), "TOP")]))
                el.append(t)
    doc.build(el, onFirstPage=frame, onLaterPages=frame)
    return buf.getvalue()


def rfq_pdf(rfq: dict, items: list[dict], vendor: dict, company: dict) -> bytes:
    rows = [["#", "Item", "Specification", "Qty", "Unit", "Unit price (excl. tax)", "Tax %"]] + \
           [[i["line_no"], i["item"], i.get("specification", ""), i["qty"], i["unit"], "", ""] for i in items]
    return _simple_pdf(f"Request for Quotation {rfq['id'][:8].upper()}",
                       f"To: {vendor.get('name', '')} · From: {company.get('name', '')} · Due: {rfq.get('due_utc') or 'at the earliest'}",
                       [("Items", [("table", rows, [8, 45, 50, 15, 12, 30, 15])]),
                        ("Instructions", [("text", "Quote unit prices excluding tax, applicable tax rate, delivery period and validity. "
                                                   "Mark any line you cannot supply to specification as non-compliant.")])],
                       f"{company.get('name', '')} procurement · RFQ tied to project revision {rfq.get('project_revision')}")


def comparison_xlsx(cmp: dict) -> bytes:
    from openpyxl import Workbook
    from openpyxl.styles import Font, PatternFill
    wb = Workbook(); ws = wb.active; ws.title = "Comparison"
    vend = cmp["vendors"]
    ws.append(["Vendor comparison", f"RFQ {cmp['rfq_id']}", f"project revision {cmp['project_revision']}"]); ws.append([])
    ws.append(["Vendor", "Subtotal", "Tax", "Total", "Complete", "Missing lines", "Non-compliant lines", "Delivery days"])
    for c in ws[ws.max_row]:
        c.font = Font(bold=True, color="FFFFFF"); c.fill = PatternFill("solid", fgColor=NAVY)
    for v in vend:
        ws.append([v["vendor"], v["subtotal"], v["tax"], v["total"], "yes" if v["complete"] else "no",
                   ", ".join(map(str, v["missing_lines"])), ", ".join(map(str, v["non_compliant_lines"])), v["delivery_days"]])
    rec = cmp.get("recommendation")
    ws.append([]); ws.append(["Recommendation", rec["vendor"] if rec else "none (no complete compliant quote)", rec["basis"] if rec else ""])
    l = wb.create_sheet("Lowest per line")
    l.append(["Line", "Item", "Qty", "Unit", "Lowest compliant vendor", "Unit price"])
    for it in cmp["lines"]:
        b = cmp["lowest_per_line"].get(it["line_no"]) or cmp["lowest_per_line"].get(str(it["line_no"]))
        l.append([it["line_no"], it["item"], it["qty"], it["unit"], b["vendor"] if b else "-", b["unit_price"] if b else None])
    for sh in (ws, l):
        for col in "ABCDEFGH":
            sh.column_dimensions[col].width = 22
    buf = io.BytesIO(); wb.save(buf); return buf.getvalue()


def commissioning_pdf(epc: dict, project_name: str, company: dict) -> bytes:
    secs = []
    for c in epc["checklists"]:
        rows = [["#", "Check", "Result", "Note", "By / at"]] + [[i + 1, it["item"], it["status"].upper(), it.get("note", ""),
                                                                 f"{(it.get('by') or '')[:8]} {(it.get('at') or '')[:16]}"]
                                                                for i, it in enumerate(c["items"])]
        secs.append((f"{c['kind'].capitalize()} checklist: {c['status'].replace('_', ' ')}"
                     + (f" (signed {c['signed_utc'][:10]})" if c.get("signed_utc") else ""), [("table", rows, [8, 85, 18, 40, 29])]))
    punch = [["Severity", "Description", "Status"]] + [[p["severity"], p["description"], p["status"]] for p in epc["punch"]]
    secs.append(("Punch list", [("table", punch, [25, 130, 25])] if len(punch) > 1 else [("text", "No punch items.")]))
    if epc.get("warranties"):
        secs.append(("Warranties", [("table", [["Item", "Provider", "Years", "From", "To"]] +
                                     [[w["item"], w.get("provider") or "", w["years"], w["starts"], w["ends"]] for w in epc["warranties"]],
                                     [55, 45, 15, 30, 35])]))
    return _simple_pdf(f"Testing & Commissioning Report: {project_name}", f"Status: {epc['status']} · progress {epc.get('progress_pct')} %",
                       secs, f"{company.get('name', '')} · results as recorded in SUNITE; signatures per checklist sign-off records")


def schedules_xlsx(result: dict) -> bytes:
    """Equipment schedule (from SLD), string schedule and cable schedule (Stage 10/11 deliverables)."""
    from openpyxl import Workbook
    wb = Workbook(); ws = wb.active; ws.title = "Equipment schedule"
    ws.append(["Tag", "Type", "Description", "Connects to"])
    for r in (result.get("sld") or {}).get("schedule", []):
        ws.append([r["tag"], r["type"], r["description"], r["connects_to"]])
    el = result.get("electrical") or {}
    s = wb.create_sheet("String schedule")
    s.append(["String", "Inverter", "MPPT", "Modules in series", "Voc cold (V)", "Vmp hot (V)"])
    lim = el.get("limits", {})
    for a in el.get("allocation", []):
        s.append([a["string"], a["inverter"], a["mppt"], a["modules"], round(a["modules"] * lim.get("voc_cold_v", 0), 1),
                  round(a["modules"] * lim.get("vmp_hot_v", 0), 1)])
    c = wb.create_sheet("Cable schedule")
    c.append(["Circuit", "From", "To", "Route length (m)", "Size (mm2)", "Current (A)", "Voltage drop %", "Ampacity check"])
    if el.get("dc_cable"):
        d = el["dc_cable"]
        c.append(["DC string (longest)", "PV string", "Inverter", d.get("longest_route_m"), d.get("size_mm2"),
                  None, d.get("voltage_drop_pct"), d.get("ampacity_check")])
    if el.get("ac_cable"):
        a = el["ac_cable"]
        for i in range(1, a.get("runs", 1) + 1):
            c.append([f"AC INV-{i:02d}", f"INV-{i:02d}", "ACDB-01", a.get("route_m"), a.get("size_mm2"), a.get("current_a"),
                      a.get("voltage_drop_pct"), a.get("ampacity_check")])
    for sh in wb.worksheets:
        for col in "ABCDEFGH":
            sh.column_dimensions[col].width = 24
    buf = io.BytesIO(); wb.save(buf); return buf.getvalue()


def dpr_pdf(result: dict, *, project: dict, company: dict, finance: dict | None = None, start_date: str | None = None) -> bytes:
    """Consolidated Detailed Project Report from one stored design result (+ optional finance model output)."""
    from datetime import date, timedelta
    from reportlab.lib.units import mm
    from reportlab.platypus import Image
    from backend.services.epc import WBS_TEMPLATE
    en, sh, el_, st = result.get("energy", {}), result["shading"], result.get("electrical") or {}, result.get("structural")
    tot = result["boq_totals"]
    summ = [["Item", "Value"], ["Location", f"{project.get('location', '')}"], ["Capacity", f"{result['dc_kwp']} kWp DC"
            + (f" / {result['ac_kw']} kW AC" if result.get("ac_kw") else "")], ["Modules", result["panel_count"]],
            ["Beam-weighted shading", f"{sh.get('beam_weighted_shading_pct')} %"],
            ["Year-1 energy", f"{en['annual_ac_kwh']:,.0f} kWh" if en.get("status") == "ok" else "not simulated (weather data required)"],
            ["Priced BOQ total", f"{tot['grand_total_priced_items']:,.0f}" + ("" if tot["complete"] else " (incomplete: unpriced items)")]]
    if finance:
        for k, lbl in (("irr_pct", "IRR %"), ("npv", "NPV"), ("simple_payback_years", "Payback (years)"), ("lcoe_per_kwh", "LCOE / kWh")):
            if finance.get(k) is not None:
                summ.append([lbl, finance[k]])
    secs = [("1. Executive summary", [("table", summ, [60, 120])]),
            ("2. Site and layout", [("text", f"Roof area {result['layout']['roof_area_m2']} m2; {result['layout']['rows']} rows at "
                                             f"{result['layout']['row_pitch_m']} m pitch ({result['layout']['row_gap_basis'].get('rule', '')})."),
                                    ("flow", Image(io.BytesIO(_layout_png(result)), width=165 * mm, height=105 * mm))]),
            ("3. Shading", [("text", sh.get("method", "")), ("table", [["Month"] + [str(m) for m in range(1, 13)],
                             ["%"] + [sh.get("monthly_beam_shading_pct", {}).get(str(m), "-") for m in range(1, 13)]], [14] + [13.8] * 12)])]
    if en.get("status") == "ok":
        secs.append(("4. Energy", [("table", [["Month"] + [str(m) for m in range(1, 13)], ["MWh"] + [round(x / 1000, 1) for x in en["monthly_ac_kwh"]]],
                                    [14] + [13.8] * 12), ("text", f"PR {en['performance_ratio']}, specific yield {en['specific_yield_kwh_per_kwp']} kWh/kWp. {en['uncertainty_note']}")]))
    else:
        secs.append(("4. Energy", [("text", "Energy not simulated: attach TMY / PVGIS weather to complete this section. No figure is estimated without irradiance data.")]))
    if el_.get("checks"):
        blocks = [("text", f"{el_['inverter_count']} x {el_['inverter']}, {el_['string_count']} strings x {el_['modules_per_string']} modules, DC/AC {el_['dc_ac_ratio']}: {el_['status'].upper()}"),
                  ("table", [["Check", "Value", "Limit", "Result"]] + [[c["check"], c["value"], f"{c['operator']} {c['limit']}", "PASS" if c["pass"] else "FAIL"] for c in el_["checks"]], [80, 30, 40, 30])]
        if result.get("sld"):
            blocks.append(("flow", sld_drawing(result["sld"], 180 * mm)))
        secs.append(("5. Electrical design", blocks))
    else:
        secs.append(("5. Electrical design", [("text", "Pending inverter selection.")]))
    if st:
        secs.append(("6. Structure", [("text", f"{st['system']}: {st['frames']} frames, {st['steel_tonnes']} t steel, added load "
                                                f"{st['roof_loading']['uniform_dead_load_kn_m2']} kN/m2. " + " ".join(st["flags"]))]))
    secs.append(("7. Bill of quantities", [("table", [["Code", "Item", "Qty", "Unit", "Amount"]] + [[l["code"], l["item"], "TBD" if l["qty"] is None else l["qty"], l["unit"],
                 "-" if l.get("amount") is None else f"{l['amount']:,.0f}"] for l in result["boq"]], [20, 70, 25, 15, 50])]))
    if finance and finance.get("table"):
        secs.append(("8. Financials", [("table", [["Year", "Energy kWh", "Savings", "O&M", "Net"]] + [[r["year"], r["energy_kwh"], r["savings"], r["om"], r["net_cashflow"]]
                     for r in finance["table"]], [20, 40, 40, 35, 45])]))
    d0 = date.fromisoformat(start_date) if start_date else None
    rows, ends = [["WBS", "Activity", "Duration (days)"] + (["Start", "End"] if d0 else [])], {}
    for wbs, name, phase, base, per100, dep in WBS_TEMPLATE:
        dur = max(1, round(base + per100 * result["dc_kwp"] / 100))
        if d0:
            s0 = ends[dep] if dep else d0; ends[wbs] = s0 + timedelta(days=dur)
            rows.append([wbs, name, dur, s0.isoformat(), ends[wbs].isoformat()])
        else:
            rows.append([wbs, name, dur])
    secs.append(("9. Implementation schedule", [("table", rows, [15, 85, 25] + ([27, 27] if d0 else []))]))
    secs.append(("10. Assumptions, limitations and approvals", [("text", " ".join(f"• {w}" for w in result["warnings"]))]))
    return _simple_pdf(f"Detailed Project Report: {project.get('name', result['project'])}",
                       f"{company.get('name', '')} · prepared for {project.get('client', '')} · engine {result['engine_version']} · DRAFT for review",
                       secs, f"{company.get('name', '')} · DPR generated from stored design results; preliminary until engineering sign-off")
