"""CRM (Stage 17): customers, leads, activities, quotations; one record chain from lead to EPC handover."""
from __future__ import annotations
from backend.db.database import transaction, utcnow
from .context import Principal
from .errors import ValidationFailed, Conflict, Forbidden
from ._common import new_id, get_scoped, j, unj
from . import audit, projects as P

STAGES = ("new", "contacted", "survey_scheduled", "surveyed", "design", "quoted", "negotiation", "won", "lost", "handed_over")
_NEXT = {"new": {"contacted"}, "contacted": {"survey_scheduled", "design"}, "survey_scheduled": {"surveyed"},
         "surveyed": {"design"}, "design": {"quoted"}, "quoted": {"negotiation", "quoted"}, "negotiation": {"quoted"},
         "lost": {"contacted"}}
SEGMENTS = ("residential", "commercial", "industrial", "institutional", "government", "utility")


def create_customer(con, p: Principal, *, name, segment="residential", phone=None, email=None, address=None, city=None, state=None):
    p.require("crm.manage")
    if not (name or "").strip():
        raise ValidationFailed("Customer name is required")
    if segment not in SEGMENTS:
        raise ValidationFailed(f"segment must be one of {SEGMENTS}")
    phone = "".join(ch for ch in (phone or "") if ch.isdigit() or ch == "+") or None
    email = (email or "").strip().lower() or None
    with transaction(con):
        dup = con.execute("SELECT id FROM customers WHERE org_id=? AND ((phone IS NOT NULL AND phone=?) OR (email IS NOT NULL AND email=?))",
                          (p.org_id, phone, email)).fetchone()
        if dup:
            raise Conflict(f"A customer with this phone/email already exists (id {dup['id']})")
        cid = new_id()
        con.execute("INSERT INTO customers VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                    (cid, p.org_id, name.strip()[:160], segment, phone, email, address, city, state, utcnow(), p.user_id))
        audit.for_principal(con, p, "crm.customer_created", entity_type="customer", entity_id=cid)
    return {"id": cid}


def create_lead(con, p: Principal, *, customer_id, title, source=None, est_kwp=None, owner_user_id=None, next_follow_up_utc=None):
    p.require("crm.manage")
    with transaction(con):
        get_scoped(con, "customers", customer_id, p.org_id, "Customer")
        if owner_user_id and not con.execute("SELECT 1 FROM memberships WHERE user_id=? AND org_id=?", (owner_user_id, p.org_id)).fetchone():
            raise ValidationFailed("Lead owner must be a member of your organisation")
        lid, now = new_id(), utcnow()
        con.execute("INSERT INTO leads VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    (lid, p.org_id, customer_id, title.strip()[:200], source, "new", owner_user_id or p.user_id, est_kwp,
                     None, next_follow_up_utc, None, now, now))
        audit.for_principal(con, p, "crm.lead_created", entity_type="lead", entity_id=lid)
    return {"id": lid, "stage": "new"}


def log_activity(con, p: Principal, lead_id, kind, note, next_follow_up_utc=None):
    p.require("crm.manage")
    if kind not in ("call", "meeting", "email", "whatsapp", "site_visit", "note"):
        raise ValidationFailed("Unknown activity kind")
    with transaction(con):
        get_scoped(con, "leads", lead_id, p.org_id, "Lead")
        con.execute("INSERT INTO lead_activities (org_id,lead_id,kind,note,at_utc,by_user) VALUES (?,?,?,?,?,?)",
                    (p.org_id, lead_id, kind, (note or "")[:2000], utcnow(), p.user_id))
        if next_follow_up_utc:
            con.execute("UPDATE leads SET next_follow_up_utc=?, updated_utc=? WHERE id=?", (next_follow_up_utc, utcnow(), lead_id))


def move_lead(con, p: Principal, lead_id, stage, *, note=None, lost_reason=None, design_payload=None):
    p.require("crm.manage")
    if stage not in STAGES or stage in ("won", "handed_over"):
        raise ValidationFailed("Use accept_quotation to win a lead and EPC handover to hand it over")
    with transaction(con):
        ld = get_scoped(con, "leads", lead_id, p.org_id, "Lead")
        cur = ld["stage"]
        if stage == "lost":
            if cur in ("won", "handed_over"):
                raise Conflict("A won lead cannot be marked lost")
            if not lost_reason:
                raise ValidationFailed("lost_reason is required")
        elif stage not in _NEXT.get(cur, set()):
            raise Conflict(f"Cannot move a lead from '{cur}' to '{stage}'")
        project_id = ld["project_id"]
        if stage == "design" and not project_id:
            project_id = P.create(con, p, name=ld["title"], payload=design_payload or {"lead_id": lead_id},
                                  in_transaction=True)["id"]
        con.execute("UPDATE leads SET stage=?, project_id=?, lost_reason=?, updated_utc=? WHERE id=?",
                    (stage, project_id, lost_reason, utcnow(), lead_id))
        con.execute("INSERT INTO lead_activities (org_id,lead_id,kind,note,at_utc,by_user) VALUES (?,?,?,?,?,?)",
                    (p.org_id, lead_id, "note", f"Stage {cur} -> {stage}. {note or ''}".strip(), utcnow(), p.user_id))
        audit.for_principal(con, p, "crm.lead_stage", entity_type="lead", entity_id=lead_id, detail={"from": cur, "to": stage})
    return {"stage": stage, "project_id": project_id}


def create_quotation(con, p: Principal, lead_id, *, model, total, currency="INR", valid_until=None, snapshot: dict):
    p.require("proposal.issue")
    if model not in ("capex", "emi", "boot", "resco", "group_captive"):
        raise ValidationFailed("Unknown commercial model")
    if total is None or total <= 0:
        raise ValidationFailed("Quotation total must be positive (price every BOQ line first)")
    with transaction(con):
        ld = get_scoped(con, "leads", lead_id, p.org_id, "Lead")
        if not ld["project_id"]:
            raise Conflict("Link a design project before quoting")
        if ld["stage"] not in ("design", "quoted", "negotiation"):
            raise Conflict(f"Cannot quote a lead in stage '{ld['stage']}'")
        prj = con.execute("SELECT revision FROM projects WHERE id=?", (ld["project_id"],)).fetchone()
        ver = (con.execute("SELECT MAX(version) FROM quotations WHERE lead_id=?", (lead_id,)).fetchone()[0] or 0) + 1
        con.execute("UPDATE quotations SET status='superseded' WHERE lead_id=? AND status IN ('draft','approved','sent')", (lead_id,))
        qid = new_id()
        con.execute("INSERT INTO quotations VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    (qid, p.org_id, lead_id, ld["project_id"], prj["revision"], ver, model, float(total), currency,
                     valid_until, j(snapshot), "draft", p.user_id, None, utcnow()))
        if ld["stage"] == "design":
            con.execute("UPDATE leads SET stage='quoted', updated_utc=? WHERE id=?", (utcnow(), lead_id))
        audit.for_principal(con, p, "crm.quotation_created", entity_type="quotation", entity_id=qid, detail={"version": ver, "total": total})
    return {"id": qid, "version": ver, "project_revision": prj["revision"]}


def approve_quotation(con, p: Principal, qid):
    p.require("quotation.approve")
    with transaction(con):
        q = get_scoped(con, "quotations", qid, p.org_id, "Quotation")
        if q["status"] != "draft":
            raise Conflict(f"Only drafts can be approved (status {q['status']})")
        if q["created_by"] == p.user_id:
            raise Forbidden("A quotation must be approved by someone other than its author")
        con.execute("UPDATE quotations SET status='approved', approved_by=? WHERE id=?", (p.user_id, qid))
        audit.for_principal(con, p, "crm.quotation_approved", entity_type="quotation", entity_id=qid)


def mark_sent(con, p: Principal, qid):
    p.require("proposal.issue")
    with transaction(con):
        q = get_scoped(con, "quotations", qid, p.org_id, "Quotation")
        if q["status"] != "approved":
            raise Conflict("Only approved quotations can be sent to customers")
        con.execute("UPDATE quotations SET status='sent' WHERE id=?", (qid,))


def record_decision(con, p: Principal, qid, accepted: bool, note=""):
    p.require("crm.manage")
    with transaction(con):
        q = get_scoped(con, "quotations", qid, p.org_id, "Quotation")
        if q["status"] != "sent":
            raise Conflict("Customer decision can only be recorded on a sent quotation")
        con.execute("UPDATE quotations SET status=? WHERE id=?", ("accepted" if accepted else "rejected", qid))
        if accepted:
            con.execute("UPDATE leads SET stage='won', updated_utc=? WHERE id=?", (utcnow(), q["lead_id"]))
        else:
            con.execute("UPDATE leads SET stage='negotiation', updated_utc=? WHERE id=?", (utcnow(), q["lead_id"]))
        audit.for_principal(con, p, "crm.quotation_" + ("accepted" if accepted else "rejected"), entity_type="quotation",
                            entity_id=qid, detail={"note": note})


def pipeline(con, p: Principal):
    p.require("project.read")
    by = {r["stage"]: {"count": r["n"], "est_kwp": round(r["kw"] or 0, 2)} for r in con.execute(
        "SELECT stage, COUNT(*) n, SUM(est_kwp) kw FROM leads WHERE org_id=? GROUP BY stage", (p.org_id,))}
    overdue = con.execute("SELECT COUNT(*) FROM leads WHERE org_id=? AND next_follow_up_utc<? AND stage NOT IN ('won','lost','handed_over')",
                          (p.org_id, utcnow())).fetchone()[0]
    return {"stages": {s: by.get(s, {"count": 0, "est_kwp": 0}) for s in STAGES}, "overdue_follow_ups": overdue}


def list_leads(con, p: Principal, stage=None):
    p.require("project.read")
    sql = ("SELECT l.*, c.name customer_name, c.segment FROM leads l JOIN customers c ON c.id=l.customer_id WHERE l.org_id=?"
           + (" AND l.stage=?" if stage else "") + " ORDER BY l.updated_utc DESC LIMIT 500")
    return [dict(r) for r in con.execute(sql, (p.org_id, stage) if stage else (p.org_id,))]


def lead_detail(con, p: Principal, lead_id):
    p.require("project.read")
    ld = dict(get_scoped(con, "leads", lead_id, p.org_id, "Lead"))
    ld["activities"] = [dict(r) for r in con.execute("SELECT kind,note,at_utc,by_user FROM lead_activities WHERE lead_id=? ORDER BY id DESC", (lead_id,))]
    ld["quotations"] = [{**dict(r), "snapshot": unj(r["snapshot"])} for r in con.execute(
        "SELECT * FROM quotations WHERE lead_id=? ORDER BY version DESC", (lead_id,))]
    return ld
