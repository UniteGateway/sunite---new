"""HTTP routes for CRM (17), procurement (14), EPC (18), commercial models (15) and BESS (16).

Each route: authenticate -> call one service function -> map ServiceError to HTTP status.
Request bodies are JSON objects whose keys are the service function's keyword arguments."""
from __future__ import annotations
import inspect
from fastapi import Header, HTTPException, Request
from fastapi.responses import Response
from backend.api.identity_routes import db, principal, run
from backend.services import crm as C, procurement as PR, epc as E, projects as P
from backend.engine import finance as F, bess as B
from backend.engine.pipeline import run_design, legacy_to_request

MAX_KEYS = 60


def _kwargs(fn, body: dict, skip: int):
    if not isinstance(body, dict) or len(body) > MAX_KEYS:
        raise HTTPException(422, "Body must be a JSON object")
    allowed = list(inspect.signature(fn).parameters)[skip:]
    bad = [k for k in body if k not in allowed]
    if bad:
        raise HTTPException(422, f"Unknown field(s): {', '.join(bad)}")
    return body


def _design_for_project(con, p, project_id):
    """Run the engine on the stored project payload (v0.2 Site or v0.3 request)."""
    prj = P.get(con, p, project_id)
    pl = prj["payload"]
    try:
        req = pl if "site" in pl else legacy_to_request({**pl, "obstacles": pl.get("obstacles", [])})
        return run_design(req)
    except (KeyError, ValueError) as e:
        raise HTTPException(422, f"Stored project cannot be designed: {e}")


# (method, path, service fn, path params (in order), positional extras)
ROUTES = [
    ("POST", "/api/crm/customers", C.create_customer, []),
    ("POST", "/api/crm/leads", C.create_lead, []),
    ("GET", "/api/crm/leads", C.list_leads, []),
    ("GET", "/api/crm/pipeline", C.pipeline, []),
    ("GET", "/api/crm/leads/{lead_id}", C.lead_detail, ["lead_id"]),
    ("POST", "/api/crm/leads/{lead_id}/stage", C.move_lead, ["lead_id"]),
    ("POST", "/api/crm/leads/{lead_id}/activities", C.log_activity, ["lead_id"]),
    ("POST", "/api/crm/leads/{lead_id}/quotations", C.create_quotation, ["lead_id"]),
    ("POST", "/api/crm/quotations/{qid}/approve", C.approve_quotation, ["qid"]),
    ("POST", "/api/crm/quotations/{qid}/send", C.mark_sent, ["qid"]),
    ("POST", "/api/crm/quotations/{qid}/decision", C.record_decision, ["qid"]),
    ("POST", "/api/procurement/vendors", PR.create_vendor, []),
    ("POST", "/api/procurement/vendors/{vid}/approve", PR.approve_vendor, ["vid"]),
    ("GET", "/api/procurement/rfqs/{rid}/emails", PR.rfq_email_drafts, ["rid"]),
    ("POST", "/api/procurement/rfqs/{rid}/quotes/{vendor_id}", PR.enter_quote, ["rid", "vendor_id"]),
    ("GET", "/api/procurement/rfqs/{rid}/comparison", PR.compare, ["rid"]),
    ("POST", "/api/procurement/rfqs/{rid}/po/{vendor_id}", PR.create_po, ["rid", "vendor_id"]),
    ("POST", "/api/procurement/pos/{po_id}/approve", PR.approve_po, ["po_id"]),
    ("POST", "/api/procurement/pos/{po_id}/issue", PR.issue_po, ["po_id"]),
    ("POST", "/api/procurement/pos/{po_id}/receive", PR.receive, ["po_id"]),
    ("POST", "/api/procurement/stock/issue", PR.issue_stock, []),
    ("GET", "/api/procurement/inventory", PR.inventory, []),
    ("GET", "/api/procurement/price-history/{code}", PR.price_history, ["code"]),
    ("GET", "/api/epc", E.list_epc, []),
    ("GET", "/api/epc/{eid}", E.summary, ["eid"]),
    ("POST", "/api/epc/{eid}/tasks/{wbs}", E.update_task, ["eid", "wbs"]),
    ("POST", "/api/epc/{eid}/logs", E.add_log, ["eid"]),
    ("POST", "/api/epc/{eid}/punch", E.add_punch, ["eid"]),
    ("POST", "/api/epc/{eid}/punch/{punch_id}/close", E.close_punch, ["eid", "punch_id"]),
    ("POST", "/api/epc/{eid}/checklists/{kind}/items/{index}", E.set_check, ["eid", "kind", "index"]),
    ("POST", "/api/epc/{eid}/checklists/{kind}/sign-off", E.sign_off, ["eid", "kind"]),
    ("POST", "/api/epc/{eid}/handover", E.handover, ["eid"]),
]


def _make(fn, params, method):
    skip = 2 + len(params)                      # (con, principal, *path params)

    def handler(request: Request = None, authorization: str | None = Header(default=None), body: dict | None = None, **path):
        args = [int(path[k]) if k in ("index", "punch_id") else path[k] for k in params]
        kw = _kwargs(fn, body or {}, skip) if method == "POST" else {k: v for k, v in (getattr(request, "query_params", {}) or {}).items()
                                                                    if k in inspect.signature(fn).parameters}
        with db() as con:
            return run(lambda: fn(con, principal(con, authorization, request), *args, **kw))
    return handler


def register(app):
    for method, path, fn, params in ROUTES:
        h = _make(fn, params, method)
        # expose path params with real names so FastAPI routes them
        sig = [inspect.Parameter(k, inspect.Parameter.KEYWORD_ONLY, annotation=str) for k in params]
        base = [inspect.Parameter("request", inspect.Parameter.KEYWORD_ONLY, annotation=Request, default=None),
                inspect.Parameter("authorization", inspect.Parameter.KEYWORD_ONLY, annotation=str | None, default=Header(default=None))]
        if method == "POST":
            base.append(inspect.Parameter("body", inspect.Parameter.KEYWORD_ONLY, annotation=dict | None, default=None))
        h.__signature__ = inspect.Signature(sig + base)
        h.__name__ = f"{method.lower()}_{fn.__module__.rsplit('.', 1)[-1]}_{fn.__name__}"
        getattr(app, method.lower())(path)(h)

    # ---- routes that need the engine run on the stored project ----
    @app.post("/api/procurement/rfqs")
    def _rfq(request: Request = None, authorization: str | None = Header(default=None), body: dict | None = None):
        b = body or {}
        with db() as con:
            p = run(lambda: principal(con, authorization, request))
            d = _design_for_project(con, p, b.get("project_id", ""))
            return run(lambda: PR.create_rfq(con, p, project_id=b["project_id"], boq_lines=d["boq"],
                                             vendor_ids=b.get("vendor_ids", []), due_utc=b.get("due_utc")))

    @app.post("/api/epc")
    def _epc(request: Request = None, authorization: str | None = Header(default=None), body: dict | None = None):
        b = body or {}
        with db() as con:
            p = run(lambda: principal(con, authorization, request))
            d = _design_for_project(con, p, b.get("project_id", ""))
            return run(lambda: E.create_from_project(con, p, project_id=b["project_id"], start_date=b.get("start_date", ""),
                                                     dc_kwp=d["dc_kwp"], electrical=d.get("electrical"),
                                                     lead_id=b.get("lead_id"), quotation_id=b.get("quotation_id")))

    # ---- stateless commercial & BESS calculators ----
    calc = {"financed_purchase": F.financed_purchase, "ppa": F.ppa_model, "group_captive": F.group_captive_model,
            "capex": F.capex_model, "metering": F.metering_split, "loan": F.loan_schedule}

    @app.post("/api/v3/finance/{model}")
    def _fin(model: str, body: dict | None = None):
        fn = calc.get(model)
        if not fn:
            raise HTTPException(404, f"Unknown model; available: {sorted(calc)}")
        try:
            return fn(**(body or {}))
        except (TypeError, ValueError) as e:
            raise HTTPException(422, str(e))

    @app.post("/api/v3/bess/dispatch")
    def _bess(body: dict | None = None):
        b = dict(body or {})
        if len(b.get("pv_kw", [])) > 8784 * 4:
            raise HTTPException(422, "Series too long (max one year at 15-min resolution)")
        try:
            sweep = b.pop("sweep_kwh", None)
            if sweep:
                return {"sweep": B.size_sweep(b.pop("pv_kw"), b.pop("load_kw"), sweep, **b)}
            return B.simulate_dispatch(b.pop("pv_kw"), b.pop("load_kw"), **b)
        except (TypeError, ValueError, KeyError) as e:
            raise HTTPException(422, str(e))

    @app.post("/api/v3/export/sld.svg")
    def _sld_svg(body: dict | None = None):
        r = _engine(body)
        if not r.get("sld"):
            raise HTTPException(422, "Select an inverter so the string design (and SLD) can be produced")
        return Response(r["sld"]["svg"], media_type="image/svg+xml",
                        headers={"Content-Disposition": 'attachment; filename="sunite-sld.svg"'})

    @app.post("/api/v3/export/sld.dxf")
    def _sld_dxf(body: dict | None = None):
        r = _engine(body)
        if not r.get("sld"):
            raise HTTPException(422, "Select an inverter so the string design (and SLD) can be produced")
        return Response(r["sld"]["dxf"], media_type="application/dxf",
                        headers={"Content-Disposition": 'attachment; filename="sunite-sld.dxf"'})


def _engine(body):
    try:
        return run_design(body or {})
    except (KeyError, ValueError, TypeError) as e:
        raise HTTPException(422, f"Invalid design request: {e}")


def register_reports(app):
    from backend.engine.exports import proposal_pdf

    @app.post("/api/v3/export/dpr.pdf")
    def _dpr(body: dict | None = None):
        """body: {design: <v3 request>, project: {name, client, location}, company: {name}, start_date, finance_inputs: {...capex_model args}}"""
        from backend.engine.exports import dpr_pdf
        b = body or {}
        r = _engine(b.get("design"))
        fin = None
        if b.get("finance_inputs"):
            if r["energy"].get("status") != "ok":
                raise HTTPException(422, "Financial section needs simulated energy (weather data)")
            try:
                fin = F.capex_model(year1_kwh=r["energy"]["annual_ac_kwh"], **b["finance_inputs"])
            except (TypeError, ValueError) as e:
                raise HTTPException(422, f"Finance inputs: {e}")
        pdf = dpr_pdf(r, project=b.get("project", {}), company=b.get("company", {}), finance=fin, start_date=b.get("start_date"))
        return Response(pdf, media_type="application/pdf", headers={"Content-Disposition": 'attachment; filename="sunite-dpr.pdf"'})

    @app.post("/api/v3/export/proposal.pdf")
    def _proposal(body: dict | None = None):
        """body: {design: <v3 request>, customer: {...}, company: {...}, quotation: {...},
                  commercial: {model: capex|emi|boot|resco|group_captive, inputs: {...}}}"""
        b = body or {}
        r = _engine(b.get("design"))
        com = b.get("commercial") or {}
        model, inputs = com.get("model", "capex"), com.get("inputs") or {}
        fn = {"capex": F.capex_model, "emi": F.financed_purchase, "boot": F.ppa_model, "resco": F.ppa_model,
              "group_captive": F.group_captive_model}.get(model)
        if not fn:
            raise HTTPException(422, "Unknown commercial model")
        if r["energy"].get("status") != "ok" and "year1_kwh" not in inputs and "year1_value" not in inputs:
            raise HTTPException(422, "Commercial figures need simulated energy (weather data) or an explicit year-1 energy input")
        try:
            out = fn(**inputs)
        except (TypeError, ValueError) as e:
            raise HTTPException(422, f"Commercial inputs: {e}")
        out.update(model=model, assumptions=com.get("assumptions") or {})
        pdf = proposal_pdf(r, customer=b.get("customer", {}), company=b.get("company", {}), commercial=out,
                           quotation=b.get("quotation", {}))
        return Response(pdf, media_type="application/pdf", headers={"Content-Disposition": 'attachment; filename="sunite-proposal.pdf"'})


def register_more_reports(app):
    from backend.engine.exports import rfq_pdf, comparison_xlsx, commissioning_pdf, schedules_xlsx
    from backend.services._common import get_scoped, unj
    XLSX = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"

    @app.post("/api/v3/export/schedules.xlsx")
    def _sched(body: dict | None = None):
        return Response(schedules_xlsx(_engine(body)), media_type=XLSX,
                        headers={"Content-Disposition": 'attachment; filename="sunite-schedules.xlsx"'})

    @app.get("/api/procurement/rfqs/{rid}/vendors/{vendor_id}/rfq.pdf")
    def _rfqpdf(rid: str, vendor_id: str, request: Request = None, authorization: str | None = Header(default=None)):
        with db() as con:
            p = run(lambda: principal(con, authorization, request))
            r = run(lambda: get_scoped(con, "rfqs", rid, p.org_id, "RFQ"))
            v = run(lambda: get_scoped(con, "vendors", vendor_id, p.org_id, "Vendor"))
            org = con.execute("SELECT name FROM organizations WHERE id=?", (p.org_id,)).fetchone()
            pdf = rfq_pdf(dict(r), unj(r["items"]), dict(v), {"name": org["name"]})
        return Response(pdf, media_type="application/pdf", headers={"Content-Disposition": 'attachment; filename="rfq.pdf"'})

    @app.get("/api/procurement/rfqs/{rid}/comparison.xlsx")
    def _cmpx(rid: str, request: Request = None, authorization: str | None = Header(default=None)):
        with db() as con:
            c = run(lambda: PR.compare(con, principal(con, authorization, request), rid))
        return Response(comparison_xlsx(c), media_type=XLSX, headers={"Content-Disposition": 'attachment; filename="vendor-comparison.xlsx"'})

    @app.get("/api/epc/{eid}/commissioning.pdf")
    def _comm(eid: str, request: Request = None, authorization: str | None = Header(default=None)):
        with db() as con:
            p = run(lambda: principal(con, authorization, request))
            e = run(lambda: E.summary(con, p, eid))
            name = con.execute("SELECT name FROM projects WHERE id=?", (e["project_id"],)).fetchone()["name"]
            org = con.execute("SELECT name FROM organizations WHERE id=?", (p.org_id,)).fetchone()["name"]
        return Response(commissioning_pdf(e, name, {"name": org}), media_type="application/pdf",
                        headers={"Content-Disposition": 'attachment; filename="commissioning-report.pdf"'})


def register_ai(app):
    from backend.ai import assistant as AI

    @app.post("/api/ai/ask")
    def _ask(request: Request = None, authorization: str | None = Header(default=None), body: dict | None = None):
        b = body or {}
        with db() as con:
            p = run(lambda: principal(con, authorization, request))
            return run(lambda: AI.ask(con, p, str(b.get("question", "")), project_id=b.get("project_id")))

    @app.post("/api/ai/proposals/{proposal_id}/decide")
    def _decide(proposal_id: str, request: Request = None, authorization: str | None = Header(default=None), body: dict | None = None):
        with db() as con:
            p = run(lambda: principal(con, authorization, request))
            return run(lambda: AI.decide_proposal(con, p, proposal_id, bool((body or {}).get("approve"))))


def register_ai_vision(app):
    import base64, os
    from backend.ai import vision
    from backend.ai.providers import default_provider, ProviderUnavailable, ProviderError
    from backend.db.database import transaction, utcnow
    from backend.services._common import new_id, j

    @app.get("/api/ai/status")
    def _status():
        on = bool(os.environ.get("GEMINI_API_KEY"))
        return {"configured": on, "model": os.environ.get("GEMINI_MODEL", "gemini-flash-latest") if on else None,
                "satellite_configured": bool(os.environ.get("SUNITE_TILE_SATELLITE_URL"))}

    @app.post("/api/v3/geo/ai-detect")
    def _detect(request: Request = None, authorization: str | None = Header(default=None), body: dict | None = None):
        b = body or {}
        view = {k: b.get(k) for k in ("center_lat", "center_lon", "zoom", "width_px", "height_px")}
        try:
            view = {"center_lat": float(view["center_lat"]), "center_lon": float(view["center_lon"]), "zoom": int(round(view["zoom"])),
                    "width_px": int(view["width_px"]), "height_px": int(view["height_px"])}
        except (TypeError, ValueError):
            raise HTTPException(422, "View needs center_lat, center_lon, zoom, width_px, height_px")
        with db() as con:
            p = run(lambda: principal(con, authorization, request))
            run(lambda: p.require("design.layout"))
            limit = int(os.environ.get("AI_DAILY_REQUESTS_PER_ORG", "500"))
            if con.execute("SELECT COUNT(*) FROM ai_conversations WHERE org_id=? AND at_utc>=?", (p.org_id, utcnow()[:10])).fetchone()[0] >= limit:
                raise HTTPException(429, "Daily AI request limit reached for your organisation")
            try:
                prov = default_provider()
            except ProviderUnavailable as e:
                raise HTTPException(503, f"AI detection not configured: {e}")
            try:
                if b.get("image_b64"):
                    png = base64.b64decode(b["image_b64"], validate=True)
                else:
                    url = os.environ.get("SUNITE_TILE_SATELLITE_URL")
                    if not url:
                        raise HTTPException(422, "No satellite imagery provider configured (SUNITE_TILE_SATELLITE_URL); upload an image of this exact view instead")
                    png = vision.compose_view(url, view["center_lat"], view["center_lon"], view["zoom"], view["width_px"], view["height_px"])
                fc = vision.detect(prov, png, view, float(b.get("min_confidence", 0.3)))
            except ProviderError as e:
                raise HTTPException(502, str(e))
            except ValueError as e:
                raise HTTPException(422, str(e))
            with transaction(con):
                con.execute("INSERT INTO ai_conversations VALUES (?,?,?,?,?,?,?,?,?,?)",
                            (new_id(), p.org_id, p.user_id, None, "roof detection", f"{len(fc['features'])} features",
                             j([]), j({"usage": fc.get("usage", {}), "rejected": len(fc["rejected"])}), f"gemini:{prov.model}", utcnow()))
            return fc


def register_layout_edit(app):
    from backend.engine.layout_edit import validate_layout, compare_scenarios

    @app.post("/api/v3/layout/validate")
    def _val(body: dict | None = None):
        b = body or {}
        try:
            if len(b.get("footprints", {})) > 20000:
                raise ValueError("Too many modules")
            return validate_layout(b["roof"], b.get("obstacles", []), b["footprints"], setback_m=float(b.get("setback_m", 0.5)),
                                   clearance_m=float(b.get("clearance_m", 0.6)), locked=set(b.get("locked", [])))
        except (KeyError, ValueError, TypeError) as e:
            raise HTTPException(422, f"Invalid layout: {e}")

    @app.post("/api/v3/scenarios/compare")
    def _cmp(body: dict | None = None):
        sc = (body or {}).get("scenarios") or {}
        if not 1 <= len(sc) <= 6:
            raise HTTPException(422, "Provide 1-6 named scenarios (v3 design requests)")
        return compare_scenarios({k: _engine(v) for k, v in sc.items()})
