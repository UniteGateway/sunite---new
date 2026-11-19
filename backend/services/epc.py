"""EPC execution (Stage 18): an approved design becomes a controlled execution project.

Gates: EPC can only start from an APPROVED project (and a WON lead, if linked). Handover needs the
commissioning checklist signed off by an electrical engineer, the handover checklist signed, and no
open critical/major punch items. The commissioning checklist is generated per string from the design."""
from __future__ import annotations
from datetime import date, timedelta
from backend.db.database import transaction, utcnow
from .context import Principal
from .errors import ValidationFailed, Conflict, Forbidden
from ._common import new_id, get_scoped, j, unj
from . import audit

# (wbs, name, phase, base_days, days_per_100kwp, depends_on)
WBS_TEMPLATE = [
    ("1.1", "Design freeze & approvals", "engineering", 3, 1, None),
    ("1.2", "Utility / net-metering application", "approvals", 5, 0, "1.1"),
    ("2.1", "Procurement & delivery", "procurement", 10, 3, "1.1"),
    ("3.1", "Structure installation", "civil", 3, 4, "2.1"),
    ("3.2", "Module mounting", "mechanical", 2, 4, "3.1"),
    ("4.1", "DC cabling & string connection", "electrical", 2, 3, "3.2"),
    ("4.2", "Inverter, ACDB & AC cabling", "electrical", 2, 2, "3.2"),
    ("4.3", "Earthing & lightning protection", "electrical", 2, 1, "3.1"),
    ("5.1", "Pre-commissioning tests", "testing", 1, 1, "4.1"),
    ("5.2", "Commissioning & grid synchronisation", "commissioning", 1, 1, "5.1"),
    ("6.1", "Handover & documentation", "handover", 1, 0, "5.2"),
]
QUALITY_ITEMS = ["Structure alignment, levels and fastener torque", "Module clamping per manufacturer manual",
                 "Cable routing, UV protection and tray support", "Connector crimping and polarity marking",
                 "Earthing continuity of frames and structures", "Labels and danger signage"]
HANDOVER_ITEMS = ["As-built layout drawing", "As-built SLD", "Test & commissioning reports", "Equipment warranty certificates",
                  "O&M manual and customer training", "Monitoring portal access handed over", "Net-meter / utility approval copy"]


def _items(names):
    return [{"item": n, "status": "pending", "note": "", "by": None, "at": None} for n in names]


def commissioning_items(electrical: dict | None):
    items = []
    if electrical and electrical.get("allocation"):
        for s in electrical["allocation"]:
            tag = f"{s['string']} (INV-{s['inverter']:02d}/MPPT{s['mppt']}, {s['modules']} modules)"
            items += [f"{tag}: polarity check", f"{tag}: open-circuit voltage within expected range",
                      f"{tag}: short-circuit / operating current", f"{tag}: insulation resistance"]
        items += [f"INV-{i:02d}: grid protection settings verified" for i in range(1, electrical["inverter_count"] + 1)]
    else:
        items.append("String-level tests (design has no string allocation: add strings before commissioning)")
    items += ["Earth electrode resistance measured", "ACDB protection devices operation tested", "Anti-islanding test",
              "Energy meter reading and direction verified", "Monitoring data reporting confirmed"]
    return _items(items)


def create_from_project(con, p: Principal, *, project_id, start_date: str, dc_kwp: float, electrical: dict | None = None,
                        lead_id=None, quotation_id=None):
    p.require("epc.manage")
    try:
        start = date.fromisoformat(start_date)
    except ValueError:
        raise ValidationFailed("start_date must be YYYY-MM-DD")
    with transaction(con):
        prj = get_scoped(con, "projects", project_id, p.org_id, "Project")
        if prj["status"] != "approved":
            raise Conflict("Only an APPROVED design can become an execution project")
        if con.execute("SELECT 1 FROM epc_projects WHERE project_id=?", (project_id,)).fetchone():
            raise Conflict("This project already has an execution record")
        if lead_id:
            ld = get_scoped(con, "leads", lead_id, p.org_id, "Lead")
            if ld["stage"] != "won" or ld["project_id"] != project_id:
                raise Conflict("Lead must be WON and linked to this project")
            if quotation_id:
                q = get_scoped(con, "quotations", quotation_id, p.org_id, "Quotation")
                if q["status"] != "accepted" or q["lead_id"] != lead_id:
                    raise Conflict("Quotation must be the accepted one for this lead")
        eid = new_id()
        con.execute("INSERT INTO epc_projects VALUES (?,?,?,?,?,?,?,?,?)",
                    (eid, p.org_id, project_id, lead_id, quotation_id, "active", start.isoformat(), utcnow(), None))
        ends: dict = {}
        for wbs, name, phase, base, per100, dep in WBS_TEMPLATE:
            dur = max(1, round(base + per100 * dc_kwp / 100))
            s = ends[dep] if dep else start
            e = s + timedelta(days=dur)
            ends[wbs] = e
            con.execute("INSERT INTO epc_tasks (id,epc_id,wbs,name,phase,planned_start,planned_end,depends_on) VALUES (?,?,?,?,?,?,?,?)",
                        (new_id(), eid, wbs, name, phase, s.isoformat(), e.isoformat(), dep))
        for kind, items in (("quality", _items(QUALITY_ITEMS)), ("commissioning", commissioning_items(electrical)),
                            ("handover", _items(HANDOVER_ITEMS))):
            con.execute("INSERT INTO epc_checklists (id,epc_id,kind,items) VALUES (?,?,?,?)", (new_id(), eid, kind, j(items)))
        if lead_id:
            con.execute("UPDATE leads SET stage='handed_over', updated_utc=? WHERE id=?", (utcnow(), lead_id))
        audit.for_principal(con, p, "epc.created", entity_type="epc", entity_id=eid, detail={"project_revision": prj["revision"]})
    return {"id": eid, "planned_completion": max(ends.values()).isoformat()}


def _epc(con, p, eid):
    return get_scoped(con, "epc_projects", eid, p.org_id, "Execution project")


def update_task(con, p: Principal, eid, wbs, *, status, progress_pct=None, assignee=None):
    p.require("epc.manage")
    if status not in ("not_started", "in_progress", "done", "blocked"):
        raise ValidationFailed("invalid status")
    with transaction(con):
        _epc(con, p, eid)
        t = con.execute("SELECT * FROM epc_tasks WHERE epc_id=? AND wbs=?", (eid, wbs)).fetchone()
        if not t:
            raise ValidationFailed("Unknown WBS code")
        if status in ("in_progress", "done") and t["depends_on"]:
            dep = con.execute("SELECT status FROM epc_tasks WHERE epc_id=? AND wbs=?", (eid, t["depends_on"])).fetchone()
            if dep["status"] != "done":
                raise Conflict(f"Predecessor {t['depends_on']} is not done")
        pct = 100.0 if status == "done" else (progress_pct if progress_pct is not None else t["progress_pct"])
        con.execute("UPDATE epc_tasks SET status=?, progress_pct=?, assignee=COALESCE(?,assignee) WHERE id=?",
                    (status, max(0, min(100, pct)), assignee, t["id"]))
        audit.for_principal(con, p, "epc.task", entity_type="epc", entity_id=eid, detail={"wbs": wbs, "status": status})


def add_log(con, p: Principal, eid, *, log_date, note, manpower=None, photos=None):
    p.require("epc.manage")
    with transaction(con):
        _epc(con, p, eid)
        con.execute("INSERT INTO epc_logs (epc_id,log_date,note,manpower,photos,by_user,at_utc) VALUES (?,?,?,?,?,?,?)",
                    (eid, log_date, note[:4000], manpower, j(photos or []), p.user_id, utcnow()))


def add_punch(con, p: Principal, eid, description, severity):
    p.require("epc.manage")
    if severity not in ("critical", "major", "minor"):
        raise ValidationFailed("severity must be critical, major or minor")
    with transaction(con):
        _epc(con, p, eid)
        cur = con.execute("INSERT INTO punch_items (epc_id,description,severity,raised_by,raised_utc) VALUES (?,?,?,?,?)",
                          (eid, description[:1000], severity, p.user_id, utcnow()))
    return {"id": cur.lastrowid}


def close_punch(con, p: Principal, eid, punch_id):
    p.require("epc.manage")
    with transaction(con):
        _epc(con, p, eid)
        con.execute("UPDATE punch_items SET status='closed', closed_by=?, closed_utc=? WHERE id=? AND epc_id=?",
                    (p.user_id, utcnow(), punch_id, eid))


def set_check(con, p: Principal, eid, kind, index, status, note=""):
    p.require("epc.manage")
    if status not in ("pass", "fail", "na", "pending"):
        raise ValidationFailed("status must be pass, fail, na or pending")
    with transaction(con):
        _epc(con, p, eid)
        c = con.execute("SELECT * FROM epc_checklists WHERE epc_id=? AND kind=?", (eid, kind)).fetchone()
        if c["status"] == "signed_off":
            raise Conflict("Checklist already signed off")
        items = unj(c["items"])
        if not 0 <= index < len(items):
            raise ValidationFailed("Item index out of range")
        items[index].update(status=status, note=note[:500], by=p.user_id, at=utcnow())
        con.execute("UPDATE epc_checklists SET items=? WHERE id=?", (j(items), c["id"]))


def sign_off(con, p: Principal, eid, kind):
    p.require("design.electrical.approve" if kind == "commissioning" else "epc.manage")
    with transaction(con):
        _epc(con, p, eid)
        c = con.execute("SELECT * FROM epc_checklists WHERE epc_id=? AND kind=?", (eid, kind)).fetchone()
        bad = [i["item"] for i in unj(c["items"]) if i["status"] not in ("pass", "na")]
        if bad:
            raise Conflict(f"{len(bad)} item(s) not passed, e.g. '{bad[0]}'")
        con.execute("UPDATE epc_checklists SET status='signed_off', signed_by=?, signed_utc=? WHERE id=?", (p.user_id, utcnow(), c["id"]))
        if kind == "commissioning":
            con.execute("UPDATE epc_projects SET status='commissioned' WHERE id=?", (eid,))
        audit.for_principal(con, p, f"epc.signed_off.{kind}", entity_type="epc", entity_id=eid)


def handover(con, p: Principal, eid, *, handover_date: str, warranties: list[dict], om_plan: list[dict] | None = None):
    """warranties: [{item, provider, years, reference}]; om_plan: [{activity, every_months, occurrences}]."""
    p.require("epc.manage")
    d0 = date.fromisoformat(handover_date)
    with transaction(con):
        e = _epc(con, p, eid)
        cl = {r["kind"]: r["status"] for r in con.execute("SELECT kind,status FROM epc_checklists WHERE epc_id=?", (eid,))}
        if cl.get("commissioning") != "signed_off" or cl.get("handover") != "signed_off":
            raise Conflict("Commissioning and handover checklists must both be signed off")
        open_p = con.execute("SELECT COUNT(*) FROM punch_items WHERE epc_id=? AND status='open' AND severity IN ('critical','major')",
                             (eid,)).fetchone()[0]
        if open_p:
            raise Conflict(f"{open_p} critical/major punch item(s) still open")
        for w in warranties:
            yrs = float(w["years"])
            con.execute("INSERT INTO warranties (epc_id,item,provider,years,starts,ends,reference) VALUES (?,?,?,?,?,?,?)",
                        (eid, w["item"], w.get("provider"), yrs, d0.isoformat(), (d0 + timedelta(days=round(365.25 * yrs))).isoformat(),
                         w.get("reference")))
        for o in (om_plan or [{"activity": "Module cleaning", "every_months": 1, "occurrences": 12},
                              {"activity": "Preventive inspection (electrical & structure)", "every_months": 3, "occurrences": 4},
                              {"activity": "Annual performance review & thermography", "every_months": 12, "occurrences": 1}]):
            for k in range(1, o["occurrences"] + 1):
                due = d0 + timedelta(days=round(30.44 * o["every_months"] * k))
                con.execute("INSERT INTO om_schedule (epc_id,activity,due_date) VALUES (?,?,?)", (eid, o["activity"], due.isoformat()))
        con.execute("UPDATE epc_projects SET status='handed_over', handed_over_utc=? WHERE id=?", (utcnow(), eid))
        audit.for_principal(con, p, "epc.handed_over", entity_type="epc", entity_id=eid)


def summary(con, p: Principal, eid):
    p.require("project.read")
    e = dict(_epc(con, p, eid))
    tasks = [dict(r) for r in con.execute("SELECT wbs,name,phase,planned_start,planned_end,depends_on,status,progress_pct,assignee "
                                          "FROM epc_tasks WHERE epc_id=? ORDER BY wbs", (eid,))]
    dur = {t["wbs"]: (date.fromisoformat(t["planned_end"]) - date.fromisoformat(t["planned_start"])).days for t in tasks}
    tot = sum(dur.values()) or 1
    e["progress_pct"] = round(sum(t["progress_pct"] * dur[t["wbs"]] for t in tasks) / tot, 1)
    e["tasks"] = tasks
    e["checklists"] = [{**dict(r), "items": unj(r["items"])} for r in con.execute("SELECT kind,status,signed_by,signed_utc,items FROM epc_checklists WHERE epc_id=?", (eid,))]
    e["punch"] = [dict(r) for r in con.execute("SELECT * FROM punch_items WHERE epc_id=? ORDER BY id", (eid,))]
    e["logs"] = [dict(r) for r in con.execute("SELECT log_date,note,manpower,by_user FROM epc_logs WHERE epc_id=? ORDER BY id DESC LIMIT 50", (eid,))]
    e["warranties"] = [dict(r) for r in con.execute("SELECT item,provider,years,starts,ends,reference FROM warranties WHERE epc_id=?", (eid,))]
    e["om_schedule"] = [dict(r) for r in con.execute("SELECT activity,due_date,status FROM om_schedule WHERE epc_id=? ORDER BY due_date LIMIT 100", (eid,))]
    return e


def list_epc(con, p: Principal):
    p.require("project.read")
    return [dict(r) for r in con.execute("SELECT e.*, pr.name project_name FROM epc_projects e JOIN projects pr ON pr.id=e.project_id "
                                         "WHERE e.org_id=? ORDER BY e.created_utc DESC", (p.org_id,))]
