"""Module & inverter catalogue (Stage 9).

Workflow: draft -> pending_verification -> verified -> retired.
  * Verification needs a datasheet reference and a DIFFERENT user than the one who entered it
    (four-eyes), holding 'equipment.verify' (electrical engineers).
  * Editing a verified record re-opens it as pending_verification: an unverified change can
    never silently feed an engineering calculation.
  * Physics plausibility checks run on every save: hard errors block, warnings are returned.
Visibility: an organisation's own records plus the platform catalogue (org_id NULL)."""
from __future__ import annotations
import csv, io, json, uuid
from backend.db.database import transaction, utcnow
from .context import Principal
from .errors import ValidationFailed, NotFound, Forbidden, Conflict
from . import audit

MODULE_FIELDS = {  # name: (required, min, max)
    "pmax_w": (True, 50, 1000), "voc_v": (True, 5, 120), "vmp_v": (True, 5, 110), "isc_a": (True, 1, 30),
    "imp_a": (True, 1, 30), "beta_voc_pct": (True, -0.6, -0.1), "gamma_pmax_pct": (True, -0.7, -0.15),
    "alpha_isc_pct": (False, 0.0, 0.12), "beta_vmp_pct": (False, -0.7, -0.15), "length_m": (True, 0.5, 3.0),
    "width_m": (True, 0.3, 1.6), "thickness_m": (False, 0.003, 0.08), "weight_kg": (False, 3, 45),
    "efficiency_pct": (False, 10, 26), "max_system_v": (True, 600, 1500), "max_series_fuse_a": (False, 5, 50),
    "bifaciality_pct": (False, 0, 100), "noct_c": (False, 35, 55),
    "warranty_product_years": (False, 1, 40), "warranty_performance_years": (False, 1, 40),
}
MODULE_TEXT = {"technology"}
INVERTER_FIELDS = {
    "ac_kw": (True, 0.5, 5000), "max_dc_v": (True, 100, 1600), "mppt_min_v": (True, 50, 1500),
    "mppt_max_v": (True, 80, 1600), "start_v": (False, 0, 1500), "mppt_count": (True, 1, 32),
    "strings_per_mppt": (True, 1, 12), "max_input_current_per_mppt_a": (True, 5, 2000),
    "max_isc_per_mppt_a": (True, 5, 2500), "max_efficiency_pct": (False, 85, 99.5), "euro_efficiency_pct": (False, 85, 99.5),
    "phases": (True, 1, 3), "grid_voltage_v": (True, 100, 1000), "max_ac_current_a": (False, 1, 10000),
    "warranty_years": (False, 1, 30),
}
INVERTER_TEXT = {"ip_rating", "topology"}


def validate_specs(kind: str, specs: dict) -> tuple[dict, list[str]]:
    fields, text = (MODULE_FIELDS, MODULE_TEXT) if kind == "module" else (INVERTER_FIELDS, INVERTER_TEXT)
    errors, warnings, clean = [], [], {}
    for k in specs:
        if k not in fields and k not in text and k != "bifacial":
            errors.append(f"Unknown field '{k}'")
    for k, (req, lo, hi) in fields.items():
        v = specs.get(k)
        if v in (None, ""):
            if req:
                errors.append(f"'{k}' is required")
            continue
        try:
            v = float(v)
        except (TypeError, ValueError):
            errors.append(f"'{k}' must be a number"); continue
        if not lo <= v <= hi:
            errors.append(f"'{k}'={v} outside plausible range {lo}..{hi}")
        clean[k] = int(v) if k in ("mppt_count", "strings_per_mppt", "phases") else v
    for k in text:
        if specs.get(k):
            clean[k] = str(specs[k])[:80]
    if "bifacial" in specs:
        clean["bifacial"] = bool(specs["bifacial"])
    if errors:
        raise ValidationFailed("; ".join(errors))
    if kind == "module":
        if clean["vmp_v"] >= clean["voc_v"]:
            errors.append("Vmp must be lower than Voc")
        if clean["imp_a"] >= clean["isc_a"]:
            errors.append("Imp must be lower than Isc")
        p = clean["vmp_v"] * clean["imp_a"]
        if abs(p - clean["pmax_w"]) / clean["pmax_w"] > 0.03:
            errors.append(f"Vmp x Imp = {p:.1f} W differs from Pmax {clean['pmax_w']} W by more than 3 %")
        eff = clean["pmax_w"] / (clean["length_m"] * clean["width_m"] * 1000) * 100
        if "efficiency_pct" in clean and abs(eff - clean["efficiency_pct"]) > 1.0:
            warnings.append(f"Stated efficiency {clean['efficiency_pct']} % vs {eff:.2f} % from Pmax/area: check dimensions")
        if eff > 25.5:
            errors.append(f"Implied efficiency {eff:.1f} % is not credible for a commercial module: check dimensions/Pmax")
        if "beta_vmp_pct" not in clean:
            warnings.append("beta_Vmp not given: electrical design will use gamma_Pmax as a conservative proxy")
    else:
        if not clean["mppt_min_v"] < clean["mppt_max_v"] <= clean["max_dc_v"]:
            errors.append("Require mppt_min_v < mppt_max_v <= max_dc_v")
        if clean["max_isc_per_mppt_a"] < clean["max_input_current_per_mppt_a"]:
            errors.append("max_isc_per_mppt_a must be >= max_input_current_per_mppt_a")
        if clean["phases"] not in (1, 3):
            errors.append("phases must be 1 or 3")
    if errors:
        raise ValidationFailed("; ".join(errors))
    return clean, warnings


def _visible(con, p: Principal, eid):
    r = con.execute("SELECT * FROM equipment WHERE id=? AND (org_id=? OR org_id IS NULL)", (eid, p.org_id)).fetchone()
    if not r:
        raise NotFound("Equipment not found")
    return r


def _out(r):
    d = dict(r); d["specs"] = json.loads(d["specs"]); return d


def _event(con, eid, rev, action, specs, p, note=None):
    con.execute("INSERT INTO equipment_events (equipment_id,revision,action,specs,by_user,at_utc,note) VALUES (?,?,?,?,?,?,?)",
                (eid, rev, action, json.dumps(specs), p.user_id, utcnow(), note))


def create(con, p: Principal, *, kind, manufacturer, model, specs, datasheet_url=None, datasheet_sha256=None,
           datasheet_revision=None, platform=False):
    p.require("equipment.manage")
    if kind not in ("module", "inverter"):
        raise ValidationFailed("kind must be module or inverter")
    if platform and not p.is_system_admin:
        raise Forbidden("Only platform administrators can add to the shared catalogue")
    if not manufacturer.strip() or not model.strip():
        raise ValidationFailed("Manufacturer and model are required")
    clean, warnings = validate_specs(kind, specs)
    eid, now = str(uuid.uuid4()), utcnow()
    org = None if platform else p.org_id
    with transaction(con):
        if con.execute("SELECT 1 FROM equipment WHERE COALESCE(org_id,'*')=COALESCE(?,'*') AND kind=? AND manufacturer=? AND model=?",
                       (org, kind, manufacturer.strip(), model.strip())).fetchone():
            raise Conflict("This model already exists in the catalogue")
        con.execute("INSERT INTO equipment (id,org_id,kind,manufacturer,model,specs,datasheet_url,datasheet_sha256,datasheet_revision,"
                    "status,revision,entered_by,created_utc,updated_utc) VALUES (?,?,?,?,?,?,?,?,?,'draft',1,?,?,?)",
                    (eid, org, kind, manufacturer.strip()[:80], model.strip()[:80], json.dumps(clean), datasheet_url,
                     datasheet_sha256, datasheet_revision, p.user_id, now, now))
        _event(con, eid, 1, "created", clean, p)
        audit.for_principal(con, p, "equipment.created", entity_type="equipment", entity_id=eid,
                            detail={"kind": kind, "model": f"{manufacturer} {model}"})
    return {"id": eid, "status": "draft", "warnings": warnings}


def update(con, p: Principal, eid, *, specs, revision: int, datasheet_url=None, datasheet_sha256=None,
           datasheet_revision=None, note=None):
    p.require("equipment.manage")
    with transaction(con):
        r = _visible(con, p, eid)
        if r["org_id"] is None and not p.is_system_admin:
            raise Forbidden("Platform catalogue entries are read-only for organisations")
        if r["status"] == "retired":
            raise Conflict("Retired equipment cannot be edited")
        if r["revision"] != revision:
            raise Conflict(f"Revision conflict: record is at revision {r['revision']}")
        clean, warnings = validate_specs(r["kind"], specs)
        new_status = "pending_verification" if r["status"] in ("verified", "pending_verification") else "draft"
        rev = r["revision"] + 1
        con.execute("UPDATE equipment SET specs=?, datasheet_url=COALESCE(?,datasheet_url), datasheet_sha256=COALESCE(?,datasheet_sha256),"
                    " datasheet_revision=COALESCE(?,datasheet_revision), status=?, revision=?, verified_by=NULL, verified_utc=NULL,"
                    " entered_by=?, updated_utc=? WHERE id=?",
                    (json.dumps(clean), datasheet_url, datasheet_sha256, datasheet_revision, new_status, rev, p.user_id, utcnow(), eid))
        _event(con, eid, rev, "updated", clean, p, note)
        audit.for_principal(con, p, "equipment.updated", entity_type="equipment", entity_id=eid,
                            detail={"revision": rev, "status": new_status})
    return {"id": eid, "revision": rev, "status": new_status, "warnings": warnings}


def submit(con, p: Principal, eid):
    p.require("equipment.manage")
    with transaction(con):
        r = _visible(con, p, eid)
        if r["status"] != "draft":
            raise Conflict(f"Only drafts can be submitted (status is {r['status']})")
        if not (r["datasheet_url"] or r["datasheet_sha256"]):
            raise ValidationFailed("Attach the manufacturer datasheet (URL or file hash) before submitting")
        con.execute("UPDATE equipment SET status='pending_verification', updated_utc=? WHERE id=?", (utcnow(), eid))
        _event(con, eid, r["revision"], "submitted", json.loads(r["specs"]), p)


def verify(con, p: Principal, eid, *, revision: int, note=""):
    p.require("equipment.verify")
    with transaction(con):
        r = _visible(con, p, eid)
        if r["org_id"] is None and not p.is_system_admin:
            raise Forbidden("Platform catalogue entries are verified by platform administrators")
        if r["status"] != "pending_verification":
            raise Conflict(f"Only records pending verification can be verified (status is {r['status']})")
        if r["revision"] != revision:
            raise Conflict("The record changed since you reviewed it; review the latest revision")
        if r["entered_by"] == p.user_id:
            raise Forbidden("Four-eyes rule: the person who entered or last edited the data cannot verify it")
        con.execute("UPDATE equipment SET status='verified', verified_by=?, verified_utc=?, updated_utc=? WHERE id=?",
                    (p.user_id, utcnow(), utcnow(), eid))
        _event(con, eid, r["revision"], "verified", json.loads(r["specs"]), p, note)
        audit.for_principal(con, p, "equipment.verified", entity_type="equipment", entity_id=eid,
                            detail={"revision": r["revision"]})


def retire(con, p: Principal, eid, note=""):
    p.require("equipment.manage")
    with transaction(con):
        r = _visible(con, p, eid)
        if r["org_id"] is None and not p.is_system_admin:
            raise Forbidden("Platform catalogue entries are read-only for organisations")
        con.execute("UPDATE equipment SET status='retired', updated_utc=? WHERE id=?", (utcnow(), eid))
        _event(con, eid, r["revision"], "retired", json.loads(r["specs"]), p, note)
        audit.for_principal(con, p, "equipment.retired", entity_type="equipment", entity_id=eid)


def list_equipment(con, p: Principal, kind=None, status=None, q=None):
    p.require("project.read")
    sql, args = "SELECT * FROM equipment WHERE (org_id=? OR org_id IS NULL)", [p.org_id]
    if kind:
        sql += " AND kind=?"; args.append(kind)
    if status:
        sql += " AND status=?"; args.append(status)
    if q:
        sql += " AND (manufacturer LIKE ? OR model LIKE ?)"; args += [f"%{q}%", f"%{q}%"]
    return [_out(r) for r in con.execute(sql + " ORDER BY manufacturer, model LIMIT 500", args)]


def get(con, p: Principal, eid):
    p.require("project.read")
    d = _out(_visible(con, p, eid))
    d["history"] = [dict(e) for e in con.execute(
        "SELECT revision, action, by_user, at_utc, note FROM equipment_events WHERE equipment_id=? ORDER BY id", (eid,))]
    return d


def for_design(con, p: Principal, eid, expected_kind) -> dict:
    """Spec dict for the engine. Retired equipment is refused; 'verified' reflects catalogue status."""
    r = _visible(con, p, eid)
    if r["kind"] != expected_kind:
        raise ValidationFailed(f"Equipment {eid} is a {r['kind']}, not a {expected_kind}")
    if r["status"] == "retired":
        raise ValidationFailed(f"{r['manufacturer']} {r['model']} is retired and cannot be used in new designs")
    return {**json.loads(r["specs"]), "model": f"{r['manufacturer']} {r['model']}", "verified": r["status"] == "verified",
            "catalogue_id": r["id"], "catalogue_revision": r["revision"], "catalogue_status": r["status"]}


CSV_TEMPLATE_MODULE = ["manufacturer", "model", "datasheet_url"] + list(MODULE_FIELDS) + sorted(MODULE_TEXT)
CSV_TEMPLATE_INVERTER = ["manufacturer", "model", "datasheet_url"] + list(INVERTER_FIELDS) + sorted(INVERTER_TEXT)


def import_csv(con, p: Principal, kind: str, text: str) -> dict:
    """Bulk entry from a datasheet transcription sheet. Every row lands as a DRAFT; nothing is auto-verified."""
    p.require("equipment.manage")
    if len(text) > 1_000_000:
        raise ValidationFailed("CSV larger than 1 MB")
    rows = list(csv.DictReader(io.StringIO(text)))
    if len(rows) > 500:
        raise ValidationFailed("Maximum 500 rows per import")
    created, rejected = [], []
    for i, row in enumerate(rows, start=2):
        row = {k.strip(): (v or "").strip() for k, v in row.items() if k}
        specs = {k: v for k, v in row.items() if k not in ("manufacturer", "model", "datasheet_url") and v != ""}
        try:
            res = create(con, p, kind=kind, manufacturer=row.get("manufacturer", ""), model=row.get("model", ""),
                         specs=specs, datasheet_url=row.get("datasheet_url") or None)
            created.append({"line": i, "id": res["id"], "warnings": res["warnings"]})
        except (ValidationFailed, Conflict) as e:
            rejected.append({"line": i, "model": row.get("model"), "error": e.message})
    return {"created": created, "rejected": rejected}
