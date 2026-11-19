"""Stage 14 procurement, 17 CRM, 18 EPC: end-to-end chain and every control gate."""
from backend.services import crm as C, procurement as PR, epc as E, projects as P
from backend.services.errors import Conflict, Forbidden, ValidationFailed, NotFound
from backend.engine.pipeline import run_design, legacy_to_request
from backend.engine.electrical import design_strings
from tests.test_identity_tenancy import _db, _company, _member, _raises
from tests.test_engine_electrical_finance import MOD, INV, T

SITE = {"name": "Factory roof", "latitude": 17.385, "longitude": 78.487,
        "roof": [{"x": 0, "y": 0}, {"x": 30, "y": 0}, {"x": 30, "y": 18}, {"x": 0, "y": 18}]}


def _org():
    con = _db()
    admin, _ = _company(con, "Unite Solar", "ravi@unite.example")
    return con, admin, {r: _member(con, admin, f"{r}@unite.example", r) for r in
                        ("sales_executive", "project_manager", "procurement_executive", "electrical_engineer", "solar_designer")}


def test_lead_to_handover_single_record_chain():
    con, admin, u = _org()
    sales, pm, proc, eng = u["sales_executive"], u["project_manager"], u["procurement_executive"], u["electrical_engineer"]
    cid = C.create_customer(con, sales, name="ABC Textiles", segment="industrial", phone="+91 98480 00000")["id"]
    assert _raises(Conflict, C.create_customer, con, sales, name="ABC dup", phone="+919848000000")       # dedupe
    lid = C.create_lead(con, sales, customer_id=cid, title="ABC 70 kW rooftop", source="referral", est_kwp=70)["id"]
    assert _raises(Conflict, C.move_lead, con, sales, lid, "quoted")                                   # no skipping
    for st in ("contacted", "survey_scheduled", "surveyed"):
        C.move_lead(con, sales, lid, st)
    pid = C.move_lead(con, sales, lid, "design", design_payload=SITE)["project_id"]
    assert pid and P.get(con, pm, pid)["payload"]["name"] == "Factory roof"

    design = run_design(legacy_to_request(SITE))
    assert _raises(ValidationFailed, C.create_quotation, con, sales, lid, model="capex", total=0, snapshot={})
    q1 = C.create_quotation(con, sales, lid, model="capex", total=3_100_000, snapshot={"dc_kwp": design["dc_kwp"]})["id"]
    q2 = C.create_quotation(con, sales, lid, model="capex", total=2_950_000, snapshot={"dc_kwp": design["dc_kwp"]})["id"]
    assert _raises(Conflict, C.approve_quotation, con, pm, q1)                                       # superseded
    assert _raises(Forbidden, C.approve_quotation, con, sales, q2)                                   # sales lacks perm
    assert _raises(Conflict, C.mark_sent, con, sales, q2)                                            # not approved yet
    C.approve_quotation(con, pm, q2); C.mark_sent(con, sales, q2); C.record_decision(con, sales, q2, True)
    assert C.lead_detail(con, sales, lid)["stage"] == "won"

    # ---- procurement from the APPROVED design ----
    v1 = PR.create_vendor(con, proc, name="Vendor One", email="v1@example.com")["id"]
    v2 = PR.create_vendor(con, proc, name="Vendor Two", email="v2@example.com")["id"]
    assert _raises(Conflict, PR.create_rfq, con, proc, project_id=pid, boq_lines=design["boq"], vendor_ids=[v1])  # not approved
    P.set_status(con, pm, pid, "approved", P.get(con, pm, pid)["revision"])
    assert _raises(Conflict, PR.create_rfq, con, proc, project_id=pid, boq_lines=design["boq"], vendor_ids=[v1])  # vendor unapproved
    for v in (v1, v2):
        PR.approve_vendor(con, pm, v)
    rfq = PR.create_rfq(con, proc, project_id=pid, boq_lines=design["boq"], vendor_ids=[v1, v2])
    items = PR.compare(con, proc, rfq["id"])["lines"]
    assert {i["code"] for i in items} >= {"PV-MOD", "MS-RAIL"} and all(i["qty"] for i in items)
    assert len(PR.rfq_email_drafts(con, proc, rfq["id"])) == 2
    PR.enter_quote(con, proc, rfq["id"], v1, lines=[{"line_no": i["line_no"], "unit_price": 100.0, "tax_pct": 12} for i in items])
    PR.enter_quote(con, proc, rfq["id"], v2, lines=[{"line_no": i["line_no"], "unit_price": 90.0, "tax_pct": 12,
                                                     "compliant": i["line_no"] != 1} for i in items])
    cmp = PR.compare(con, proc, rfq["id"])
    assert cmp["recommendation"]["vendor_id"] == v1                       # v2 cheaper but non-compliant on line 1
    assert cmp["lowest_per_line"][2]["vendor_id"] == v2
    assert _raises(Conflict, PR.create_po, con, proc, rfq["id"], v2)
    po = PR.create_po(con, proc, rfq["id"], v1)
    exp = sum(100.0 * i["qty"] for i in items)
    assert abs(po["total"] - exp * 1.12) < 0.05
    assert _raises(Conflict, PR.issue_po, con, proc, po["id"])            # not approved -> never issued
    assert _raises(Forbidden, PR.approve_po, con, proc, po["id"])         # procurement exec lacks approve
    PR.approve_po(con, pm, po["id"]); PR.issue_po(con, proc, po["id"])
    first = items[0]
    PR.receive(con, proc, po["id"], 1, first["qty"] - 1)
    assert _raises(Conflict, PR.receive, con, proc, po["id"], 1, 5)       # over-receipt
    PR.receive(con, proc, po["id"], 1, 1)
    for i in items[1:]:
        r = PR.receive(con, proc, po["id"], i["line_no"], i["qty"])
    assert r["po_closed"]
    stock = {s["code"]: s["qty"] for s in PR.inventory(con, proc)}
    assert stock["PV-MOD"] == first["qty"]
    PR.issue_stock(con, proc, code="PV-MOD", qty=10, location="Main store", to_ref="site")
    assert _raises(Conflict, PR.issue_stock, con, proc, code="PV-MOD", qty=10_000, location="Main store", to_ref="site")
    assert PR.price_history(con, proc, "PV-MOD")[0]["unit_price"] in (100.0, 90.0)

    # ---- EPC execution ----
    el = design_strings(design["panel_count"], MOD, INV, T)
    epc = E.create_from_project(con, pm, project_id=pid, start_date="2026-11-10", dc_kwp=design["dc_kwp"], electrical=el,
                                lead_id=lid, quotation_id=q2)
    assert C.lead_detail(con, sales, lid)["stage"] == "handed_over"
    assert _raises(Conflict, E.create_from_project, con, pm, project_id=pid, start_date="2026-11-10", dc_kwp=1)   # no duplicate
    eid = epc["id"]
    assert _raises(Conflict, E.update_task, con, pm, eid, "3.1", status="in_progress")                           # predecessor
    s = E.summary(con, pm, eid)
    comm = next(c for c in s["checklists"] if c["kind"] == "commissioning")
    assert sum(1 for i in comm["items"] if "polarity" in i["item"]) == el["string_count"]                        # per string
    for wbs in [t["wbs"] for t in s["tasks"]]:
        E.update_task(con, pm, eid, wbs, status="done")
    assert E.summary(con, pm, eid)["progress_pct"] == 100.0
    pun = E.add_punch(con, pm, eid, "Loose MC4 on S003", "major")["id"]
    for kind in ("quality", "commissioning", "handover"):
        c = next(c for c in E.summary(con, pm, eid)["checklists"] if c["kind"] == kind)
        assert _raises(Conflict, E.sign_off, con, eng, eid, kind)
        for k in range(len(c["items"])):
            E.set_check(con, eng, eid, kind, k, "pass")
    assert _raises(Forbidden, E.sign_off, con, pm, eid, "commissioning")             # needs electrical sign-off authority
    for kind in ("quality", "commissioning", "handover"):
        E.sign_off(con, eng, eid, kind)
    assert _raises(Conflict, E.handover, con, pm, eid, handover_date="2026-12-20", warranties=[])                  # open punch
    E.close_punch(con, pm, eid, pun)
    E.handover(con, pm, eid, handover_date="2026-12-20",
               warranties=[{"item": "Modules (product)", "provider": "OEM", "years": 12}, {"item": "Inverter", "provider": "OEM", "years": 5}])
    fin = E.summary(con, pm, eid)
    assert fin["status"] == "handed_over" and fin["warranties"][0]["ends"].startswith("2038") and len(fin["om_schedule"]) == 17
    assert con.execute("SELECT COUNT(*) FROM customers").fetchone()[0] == 1
    assert con.execute("SELECT COUNT(*) FROM projects").fetchone()[0] == 1               # no duplicated records


def test_business_tenant_isolation():
    con, admin, u = _org()
    other, _ = _company(con, "Other EPC", "boss@other.example")
    cid = C.create_customer(con, admin, name="Private client")["id"]
    lid = C.create_lead(con, admin, customer_id=cid, title="Secret deal")["id"]
    assert _raises(NotFound, C.lead_detail, con, other, lid)
    assert _raises(NotFound, C.create_lead, con, other, customer_id=cid, title="steal")
    assert C.list_leads(con, other) == [] and C.pipeline(con, other)["stages"]["new"]["count"] == 0
    v = PR.create_vendor(con, admin, name="V")["id"]
    assert _raises(NotFound, PR.approve_vendor, con, other, v)


def test_lost_and_reopen():
    con, admin, u = _org()
    s = u["sales_executive"]
    lid = C.create_lead(con, s, customer_id=C.create_customer(con, s, name="X")["id"], title="t")["id"]
    assert _raises(ValidationFailed, C.move_lead, con, s, lid, "lost")
    C.move_lead(con, s, lid, "lost", lost_reason="Budget")
    C.move_lead(con, s, lid, "contacted")
    assert C.pipeline(con, s)["stages"]["contacted"]["count"] == 1


def test_stage20_report_types():
    import io
    from openpyxl import load_workbook
    from backend.engine.exports import rfq_pdf, comparison_xlsx, commissioning_pdf, schedules_xlsx
    from tests.test_engine_sld_struct_fin_bess import STRUCT
    req = legacy_to_request(SITE)
    req["inverter"] = {**INV.__dict__}; req["temps"] = {"min_ambient_c": 5, "max_cell_c": 70}
    req["cables"] = {"inverter_xy": [0, 0], "acdb_xy": [0, -10]}
    r = run_design(req)
    wb = load_workbook(io.BytesIO(schedules_xlsx(r)))
    assert wb["String schedule"].max_row - 1 == r["electrical"]["string_count"]
    assert wb["Equipment schedule"].max_row - 1 == len(r["sld"]["schedule"])
    assert wb["Cable schedule"].max_row - 1 == 1 + r["electrical"]["inverter_count"]
    items = [{"line_no": 1, "item": "PV modules", "specification": "550 W", "qty": 10, "unit": "Nos"}]
    assert rfq_pdf({"id": "abcdef12-x", "project_revision": 2}, items, {"name": "V"}, {"name": "Unite Solar"})[:4] == b"%PDF"
    cmp = {"rfq_id": "r", "project_revision": 2, "lines": items, "lowest_per_line": {1: {"vendor": "V", "unit_price": 9.0}},
           "vendors": [{"vendor": "V", "vendor_id": "v", "subtotal": 90, "tax": 10.8, "total": 100.8, "complete": True,
                        "missing_lines": [], "non_compliant_lines": [], "delivery_days": 7}],
           "recommendation": {"vendor": "V", "vendor_id": "v", "total": 100.8, "basis": "lowest"}}
    assert "Lowest per line" in load_workbook(io.BytesIO(comparison_xlsx(cmp))).sheetnames
    epc = {"status": "commissioned", "progress_pct": 100, "punch": [], "warranties": [],
           "checklists": [{"kind": "commissioning", "status": "signed_off", "signed_utc": "2026-12-01T00:00:00",
                           "items": [{"item": "S001 polarity", "status": "pass", "note": "", "by": "u", "at": "2026-12-01"}]}]}
    assert commissioning_pdf(epc, "Shed A", {"name": "Unite Solar"})[:4] == b"%PDF"
