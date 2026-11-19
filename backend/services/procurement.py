"""Procurement (Stage 14): vendors, RFQ from an APPROVED project BOQ, quotes, auditable comparison,
purchase orders with separate approval, goods receipt and inventory. Nothing binding is issued unapproved."""
from __future__ import annotations
from datetime import datetime, timezone
from backend.db.database import transaction, utcnow
from .context import Principal
from .errors import ValidationFailed, Conflict, Forbidden, NotFound
from ._common import new_id, get_scoped, j, unj
from . import audit


def create_vendor(con, p: Principal, *, name, gstin=None, email=None, phone=None, categories=""):
    p.require("procurement.manage")
    with transaction(con):
        if con.execute("SELECT 1 FROM vendors WHERE org_id=? AND name=?", (p.org_id, name.strip())).fetchone():
            raise Conflict("Vendor already exists")
        vid = new_id()
        con.execute("INSERT INTO vendors VALUES (?,?,?,?,?,?,?,0,?)", (vid, p.org_id, name.strip()[:160], gstin, email, phone, categories, utcnow()))
        audit.for_principal(con, p, "proc.vendor_created", entity_type="vendor", entity_id=vid)
    return {"id": vid, "approved": False}


def approve_vendor(con, p: Principal, vid):
    p.require("procurement.approve")
    with transaction(con):
        get_scoped(con, "vendors", vid, p.org_id, "Vendor")
        con.execute("UPDATE vendors SET approved=1 WHERE id=?", (vid,))
        audit.for_principal(con, p, "proc.vendor_approved", entity_type="vendor", entity_id=vid)


def create_rfq(con, p: Principal, *, project_id, boq_lines: list[dict], vendor_ids: list[str], due_utc=None):
    p.require("procurement.manage")
    with transaction(con):
        prj = get_scoped(con, "projects", project_id, p.org_id, "Project")
        if prj["status"] != "approved":
            raise Conflict("RFQs can only be raised from an APPROVED project revision")
        items = [{"line_no": i + 1, "code": l["code"], "item": l["item"], "specification": l.get("specification", ""),
                  "unit": l["unit"], "qty": l["qty"], "source": l.get("source")}
                 for i, l in enumerate(x for x in boq_lines if x.get("qty") not in (None, 0) and x.get("category", "material") != "service")]
        if not items:
            raise ValidationFailed("No quantified material lines to quote")
        for v in vendor_ids:
            if not get_scoped(con, "vendors", v, p.org_id, "Vendor")["approved"]:
                raise Conflict("Only approved vendors can receive RFQs")
        rid = new_id()
        con.execute("INSERT INTO rfqs VALUES (?,?,?,?,?,?,?,?,?)",
                    (rid, p.org_id, project_id, prj["revision"], "issued", due_utc, j(items), p.user_id, utcnow()))
        for v in vendor_ids:
            con.execute("INSERT INTO rfq_vendors VALUES (?,?)", (rid, v))
        audit.for_principal(con, p, "proc.rfq_issued", entity_type="rfq", entity_id=rid,
                            detail={"project_revision": prj["revision"], "lines": len(items), "vendors": len(vendor_ids)})
    return {"id": rid, "lines": len(items), "project_revision": prj["revision"]}


def rfq_email_drafts(con, p: Principal, rid, company_name="Unite Solar"):
    """Prepared text only: the app never emails vendors by itself."""
    p.require("procurement.manage")
    r = get_scoped(con, "rfqs", rid, p.org_id, "RFQ")
    items = unj(r["items"])
    table = "\n".join(f"{i['line_no']:>3}. {i['item']} | {i['specification']} | {i['qty']} {i['unit']}" for i in items)
    out = []
    for v in con.execute("SELECT v.* FROM vendors v JOIN rfq_vendors rv ON rv.vendor_id=v.id WHERE rv.rfq_id=?", (rid,)):
        out.append({"vendor": v["name"], "to": v["email"], "subject": f"RFQ {rid[:8].upper()} - {company_name}",
                    "body": f"Dear {v['name']},\n\nPlease quote unit prices (excl. tax), applicable tax %, delivery period "
                            f"and validity for:\n\n{table}\n\nDue: {r['due_utc'] or 'at the earliest'}.\n\nRegards,\n{company_name} Procurement"})
    return out


def enter_quote(con, p: Principal, rid, vendor_id, *, lines: list[dict], delivery_days=None, valid_until=None):
    p.require("procurement.manage")
    with transaction(con):
        r = get_scoped(con, "rfqs", rid, p.org_id, "RFQ")
        if r["status"] != "issued":
            raise Conflict("RFQ is closed")
        if not con.execute("SELECT 1 FROM rfq_vendors WHERE rfq_id=? AND vendor_id=?", (rid, vendor_id)).fetchone():
            raise Forbidden("This vendor was not invited to the RFQ")
        valid = {i["line_no"] for i in unj(r["items"])}
        clean = []
        for l in lines:
            if l["line_no"] not in valid:
                raise ValidationFailed(f"Unknown RFQ line {l['line_no']}")
            if l.get("unit_price") is not None and l["unit_price"] < 0:
                raise ValidationFailed("Negative price")
            clean.append({"line_no": l["line_no"], "unit_price": l.get("unit_price"), "tax_pct": l.get("tax_pct", 0.0),
                          "compliant": bool(l.get("compliant", True)), "remarks": l.get("remarks", "")})
        con.execute("DELETE FROM vendor_quotes WHERE rfq_id=? AND vendor_id=?", (rid, vendor_id))
        qid = new_id()
        con.execute("INSERT INTO vendor_quotes VALUES (?,?,?,?,?,?,?,?,?)",
                    (qid, p.org_id, rid, vendor_id, j(clean), delivery_days, valid_until, utcnow(), p.user_id))
        audit.for_principal(con, p, "proc.quote_entered", entity_type="rfq", entity_id=rid, detail={"vendor": vendor_id})
    return {"id": qid}


def compare(con, p: Principal, rid):
    p.require("project.read")
    r = get_scoped(con, "rfqs", rid, p.org_id, "RFQ")
    items = unj(r["items"])
    quotes = con.execute("SELECT q.*, v.name vname FROM vendor_quotes q JOIN vendors v ON v.id=q.vendor_id WHERE q.rfq_id=?", (rid,)).fetchall()
    vendors, best = [], {}
    for q in quotes:
        ql = {l["line_no"]: l for l in unj(q["lines"])}
        sub = tax = 0.0
        missing, noncomp = [], []
        for it in items:
            l = ql.get(it["line_no"])
            if not l or l["unit_price"] is None:
                missing.append(it["line_no"]); continue
            if not l["compliant"]:
                noncomp.append(it["line_no"]); continue
            amt = l["unit_price"] * it["qty"]
            sub += amt; tax += amt * l["tax_pct"] / 100
            b = best.get(it["line_no"])
            if b is None or l["unit_price"] < b["unit_price"]:
                best[it["line_no"]] = {"vendor": q["vname"], "vendor_id": q["vendor_id"], "unit_price": l["unit_price"]}
        vendors.append({"vendor_id": q["vendor_id"], "vendor": q["vname"], "subtotal": round(sub, 2), "tax": round(tax, 2),
                        "total": round(sub + tax, 2), "missing_lines": missing, "non_compliant_lines": noncomp,
                        "complete": not missing and not noncomp, "delivery_days": q["delivery_days"]})
    complete = [v for v in vendors if v["complete"]]
    rec = min(complete, key=lambda v: (v["total"], v["delivery_days"] or 9999)) if complete else None
    return {"rfq_id": rid, "project_revision": r["project_revision"], "lines": items, "vendors": sorted(vendors, key=lambda v: v["total"]),
            "lowest_per_line": best,
            "recommendation": {"vendor_id": rec["vendor_id"], "vendor": rec["vendor"], "total": rec["total"],
                               "basis": "lowest total among complete, compliant quotes (tie: shorter delivery)"} if rec else None}


def create_po(con, p: Principal, rid, vendor_id):
    p.require("procurement.manage")
    cmp = compare(con, p, rid)
    v = next((x for x in cmp["vendors"] if x["vendor_id"] == vendor_id), None)
    if not v:
        raise NotFound("No quote from this vendor")
    if not v["complete"]:
        raise Conflict("Vendor quote is incomplete or non-compliant; a PO cannot be raised against it")
    with transaction(con):
        q = con.execute("SELECT lines FROM vendor_quotes WHERE rfq_id=? AND vendor_id=?", (rid, vendor_id)).fetchone()
        ql = {l["line_no"]: l for l in unj(q["lines"])}
        lines = [{**it, "unit_price": ql[it["line_no"]]["unit_price"], "tax_pct": ql[it["line_no"]]["tax_pct"],
                  "amount": round(ql[it["line_no"]]["unit_price"] * it["qty"], 2)} for it in cmp["lines"]]
        year = datetime.now(timezone.utc).year
        n = con.execute("SELECT COUNT(*) FROM purchase_orders WHERE org_id=? AND number LIKE ?", (p.org_id, f"PO-{year}-%")).fetchone()[0] + 1
        pid, num = new_id(), f"PO-{year}-{n:04d}"
        prj = con.execute("SELECT project_id FROM rfqs WHERE id=?", (rid,)).fetchone()["project_id"]
        con.execute("INSERT INTO purchase_orders VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    (pid, p.org_id, num, rid, vendor_id, prj, j(lines), v["subtotal"], v["tax"], v["total"],
                     "pending_approval", p.user_id, None, None, None, utcnow()))
        audit.for_principal(con, p, "proc.po_created", entity_type="po", entity_id=pid, detail={"number": num, "total": v["total"]})
    return {"id": pid, "number": num, "total": v["total"], "status": "pending_approval"}


def approve_po(con, p: Principal, po_id):
    p.require("procurement.approve")
    with transaction(con):
        po = get_scoped(con, "purchase_orders", po_id, p.org_id, "Purchase order")
        if po["status"] != "pending_approval":
            raise Conflict(f"PO is {po['status']}")
        if po["created_by"] == p.user_id:
            raise Forbidden("A PO must be approved by someone other than its creator")
        con.execute("UPDATE purchase_orders SET status='approved', approved_by=?, approved_utc=? WHERE id=?", (p.user_id, utcnow(), po_id))
        audit.for_principal(con, p, "proc.po_approved", entity_type="po", entity_id=po_id, detail={"total": po["total"]})


def issue_po(con, p: Principal, po_id):
    p.require("procurement.manage")
    with transaction(con):
        po = get_scoped(con, "purchase_orders", po_id, p.org_id, "Purchase order")
        if po["status"] != "approved":
            raise Conflict("Only approved POs can be issued to the vendor")
        con.execute("UPDATE purchase_orders SET status='issued', issued_utc=? WHERE id=?", (utcnow(), po_id))
        audit.for_principal(con, p, "proc.po_issued", entity_type="po", entity_id=po_id)


def receive(con, p: Principal, po_id, line_no, qty, location="Main store"):
    p.require("procurement.manage")
    if qty <= 0:
        raise ValidationFailed("Received quantity must be positive")
    with transaction(con):
        po = get_scoped(con, "purchase_orders", po_id, p.org_id, "Purchase order")
        if po["status"] != "issued":
            raise Conflict("Goods can only be received against an issued PO")
        lines = {l["line_no"]: l for l in unj(po["lines"])}
        if line_no not in lines:
            raise ValidationFailed("Unknown PO line")
        got = con.execute("SELECT COALESCE(SUM(qty),0) FROM goods_receipts WHERE po_id=? AND line_no=?", (po_id, line_no)).fetchone()[0]
        if got + qty > lines[line_no]["qty"] + 1e-9:
            raise Conflict(f"Over-receipt: ordered {lines[line_no]['qty']}, already received {got}")
        now, l = utcnow(), lines[line_no]
        con.execute("INSERT INTO goods_receipts (org_id,po_id,line_no,qty,location,received_by,at_utc) VALUES (?,?,?,?,?,?,?)",
                    (p.org_id, po_id, line_no, qty, location, p.user_id, now))
        con.execute("INSERT INTO inventory VALUES (?,?,?,?,?,?) ON CONFLICT(org_id,code,location) DO UPDATE SET qty=qty+excluded.qty",
                    (p.org_id, l["code"], location, l["item"], l["unit"], qty))
        con.execute("INSERT INTO inventory_moves (org_id,code,location,qty,reason,ref,by_user,at_utc) VALUES (?,?,?,?,?,?,?,?)",
                    (p.org_id, l["code"], location, qty, "goods_receipt", po["number"], p.user_id, now))
        done = all(con.execute("SELECT COALESCE(SUM(qty),0) FROM goods_receipts WHERE po_id=? AND line_no=?", (po_id, n)).fetchone()[0]
                   >= lines[n]["qty"] - 1e-9 for n in lines)
        if done:
            con.execute("UPDATE purchase_orders SET status='closed' WHERE id=?", (po_id,))
    return {"po_closed": done}


def issue_stock(con, p: Principal, *, code, qty, location, to_ref):
    p.require("procurement.manage")
    with transaction(con):
        r = con.execute("SELECT qty FROM inventory WHERE org_id=? AND code=? AND location=?", (p.org_id, code, location)).fetchone()
        if not r or r["qty"] + 1e-9 < qty:
            raise Conflict("Insufficient stock")
        con.execute("UPDATE inventory SET qty=qty-? WHERE org_id=? AND code=? AND location=?", (qty, p.org_id, code, location))
        con.execute("INSERT INTO inventory_moves (org_id,code,location,qty,reason,ref,by_user,at_utc) VALUES (?,?,?,?,?,?,?,?)",
                    (p.org_id, code, location, -qty, "issue_to_site", to_ref, p.user_id, utcnow()))


def inventory(con, p: Principal):
    p.require("project.read")
    return [dict(r) for r in con.execute("SELECT code,item,unit,location,qty FROM inventory WHERE org_id=? ORDER BY code", (p.org_id,))]


def price_history(con, p: Principal, code):
    p.require("project.read")
    out = []
    for q in con.execute("SELECT q.lines, q.received_utc, v.name, r.items FROM vendor_quotes q JOIN vendors v ON v.id=q.vendor_id "
                         "JOIN rfqs r ON r.id=q.rfq_id WHERE q.org_id=? ORDER BY q.received_utc", (p.org_id,)):
        nos = {i["line_no"] for i in unj(q["items"]) if i["code"] == code}
        for l in unj(q["lines"]):
            if l["line_no"] in nos and l["unit_price"] is not None:
                out.append({"vendor": q["name"], "unit_price": l["unit_price"], "at": q["received_utc"]})
    return out
