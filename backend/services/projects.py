"""Tenant-scoped project repository.

Isolation rule: every read/write goes through _access(), which only returns a project when
  (a) it belongs to the caller's active organisation and the role is not grant-only, or
  (b) an explicit grant exists for the caller's user or organisation.
Anything else is reported as NotFound (never Forbidden) so project ids cannot be probed."""
from __future__ import annotations
import contextlib, json, uuid
from backend.db.database import transaction, utcnow
from backend.auth.rbac import GRANT_ONLY_ROLES
from .context import Principal
from .errors import NotFound, Conflict, ValidationFailed, Forbidden
from . import audit

MAX_PAYLOAD_BYTES = 2_000_000
STATUSES = ("draft", "in_review", "approved", "archived")


def _payload(payload) -> str:
    text = json.dumps(payload, separators=(",", ":"))
    if len(text) > MAX_PAYLOAD_BYTES:
        raise ValidationFailed("Project payload exceeds 2 MB")
    return text


def _access(con, p: Principal, project_id: str):
    row = con.execute("SELECT * FROM projects WHERE id=? AND deleted_utc IS NULL", (project_id,)).fetchone()
    if not row:
        raise NotFound("Project not found")
    if row["org_id"] == p.org_id and p.role not in GRANT_ONLY_ROLES and "project.read" in p.permissions:
        return row, ("edit" if "project.update" in p.permissions else "read")
    g = con.execute("SELECT access FROM project_grants WHERE project_id=? AND (grantee_user_id=? OR grantee_org_id=?) "
                    "ORDER BY access='edit' DESC LIMIT 1", (project_id, p.user_id, p.org_id)).fetchone()
    if g and "project.read" in p.permissions:
        return row, ("edit" if g["access"] == "edit" and "project.update" in p.permissions else "read")
    raise NotFound("Project not found")


def _out(row, access):
    return {"id": row["id"], "org_id": row["org_id"], "name": row["name"], "status": row["status"],
            "revision": row["revision"], "updated_utc": row["updated_utc"], "access": access,
            "payload": json.loads(row["payload"])}


def create(con, p: Principal, *, name, payload, engine_version=None, in_transaction=False):
    p.require("project.create")
    if not (name or "").strip():
        raise ValidationFailed("Project name is required")
    text, pid, now = _payload(payload), str(uuid.uuid4()), utcnow()
    with (contextlib.nullcontext() if in_transaction else transaction(con)):
        con.execute("INSERT INTO projects (id,org_id,name,status,payload,revision,created_by,created_utc,updated_by,updated_utc)"
                    " VALUES (?,?,?,'draft',?,1,?,?,?,?)", (pid, p.org_id, name.strip()[:200], text, p.user_id, now, p.user_id, now))
        con.execute("INSERT INTO project_revisions VALUES (?,?,?,?,?,?,?)", (pid, 1, text, engine_version, "created", p.user_id, now))
        audit.for_principal(con, p, "project.created", entity_type="project", entity_id=pid, detail={"name": name})
    return {"id": pid, "revision": 1, "updated_utc": now}


def get(con, p: Principal, project_id):
    return _out(*_access(con, p, project_id))


def list_projects(con, p: Principal, limit=200):
    p.require("project.read")
    own = [] if p.role in GRANT_ONLY_ROLES else con.execute(
        "SELECT id,org_id,name,status,revision,updated_utc FROM projects WHERE org_id=? AND deleted_utc IS NULL",
        (p.org_id,)).fetchall()
    shared = con.execute(
        "SELECT DISTINCT pr.id,pr.org_id,pr.name,pr.status,pr.revision,pr.updated_utc FROM projects pr "
        "JOIN project_grants g ON g.project_id=pr.id WHERE pr.deleted_utc IS NULL AND pr.org_id<>? "
        "AND (g.grantee_user_id=? OR g.grantee_org_id=?)", (p.org_id, p.user_id, p.org_id)).fetchall()
    if p.role in GRANT_ONLY_ROLES:
        shared = con.execute(
            "SELECT DISTINCT pr.id,pr.org_id,pr.name,pr.status,pr.revision,pr.updated_utc FROM projects pr "
            "JOIN project_grants g ON g.project_id=pr.id WHERE pr.deleted_utc IS NULL "
            "AND (g.grantee_user_id=? OR g.grantee_org_id=?)", (p.user_id, p.org_id)).fetchall()
    rows = [dict(r, shared=r["org_id"] != p.org_id) for r in list(own) + list(shared)]
    return sorted(rows, key=lambda r: r["updated_utc"], reverse=True)[:limit]


def update(con, p: Principal, project_id, *, payload, if_match: int | None, name=None, note=None, engine_version=None):
    p.require("project.update")
    text = _payload(payload)
    with transaction(con):
        row, access = _access(con, p, project_id)
        if access != "edit":
            raise Forbidden("You have read-only access to this project")
        if if_match is None or int(if_match) != row["revision"]:
            raise Conflict(f"Revision conflict: project is at revision {row['revision']}; reload before saving")
        if row["status"] in ("approved", "archived"):
            raise Conflict(f"Project is {row['status']}; reopen it as draft before editing")
        rev, now = row["revision"] + 1, utcnow()
        con.execute("UPDATE projects SET payload=?, name=?, revision=?, updated_by=?, updated_utc=? WHERE id=?",
                    (text, (name or row["name"]).strip()[:200], rev, p.user_id, now, project_id))
        con.execute("INSERT INTO project_revisions VALUES (?,?,?,?,?,?,?)",
                    (project_id, rev, text, engine_version, (note or "")[:500], p.user_id, now))
        audit.for_principal(con, p, "project.updated", entity_type="project", entity_id=project_id, detail={"revision": rev})
    return {"id": project_id, "revision": rev, "updated_utc": now}


def set_status(con, p: Principal, project_id, status, if_match: int):
    if status not in STATUSES:
        raise ValidationFailed(f"status must be one of {STATUSES}")
    p.require("project.approve" if status == "approved" else "project.update")
    with transaction(con):
        row, access = _access(con, p, project_id)
        if access != "edit" or row["org_id"] != p.org_id:
            raise Forbidden("Only the owning organisation can change project status")
        if int(if_match) != row["revision"]:
            raise Conflict("Revision conflict: reload before changing status")
        con.execute("UPDATE projects SET status=?, updated_by=?, updated_utc=? WHERE id=?", (status, p.user_id, utcnow(), project_id))
        audit.for_principal(con, p, f"project.status.{status}", entity_type="project", entity_id=project_id,
                            detail={"revision": row["revision"], "from": row["status"]})


def delete(con, p: Principal, project_id):
    p.require("project.delete")
    with transaction(con):
        row, _ = _access(con, p, project_id)
        if row["org_id"] != p.org_id:
            raise NotFound("Project not found")
        con.execute("UPDATE projects SET deleted_utc=? WHERE id=?", (utcnow(), project_id))
        audit.for_principal(con, p, "project.deleted", entity_type="project", entity_id=project_id)


def share(con, p: Principal, project_id, *, access, user_email=None, org_slug=None):
    p.require("project.share")
    if access not in ("read", "edit"):
        raise ValidationFailed("access must be read or edit")
    if bool(user_email) == bool(org_slug):
        raise ValidationFailed("Share with exactly one of user_email or org_slug")
    with transaction(con):
        row, _ = _access(con, p, project_id)
        if row["org_id"] != p.org_id:
            raise Forbidden("Only the owning organisation can share a project")
        if user_email:
            t = con.execute("SELECT id FROM users WHERE email=?", (user_email.strip().lower(),)).fetchone()
            col, val = "grantee_user_id", t["id"] if t else None
        else:
            t = con.execute("SELECT id FROM organizations WHERE slug=? AND is_active=1", (org_slug,)).fetchone()
            col, val = "grantee_org_id", t["id"] if t else None
        if not val:
            raise NotFound("Recipient not found")
        con.execute(f"DELETE FROM project_grants WHERE project_id=? AND {col}=?", (project_id, val))
        con.execute(f"INSERT INTO project_grants (project_id,{col},access,granted_by,created_utc) VALUES (?,?,?,?,?)",
                    (project_id, val, access, p.user_id, utcnow()))
        audit.for_principal(con, p, "project.shared", entity_type="project", entity_id=project_id,
                            detail={"to": user_email or org_slug, "access": access})


def revoke_share(con, p: Principal, project_id, *, user_email=None, org_slug=None):
    p.require("project.share")
    with transaction(con):
        row, _ = _access(con, p, project_id)
        if row["org_id"] != p.org_id:
            raise Forbidden("Only the owning organisation can revoke sharing")
        con.execute("DELETE FROM project_grants WHERE project_id=? AND (grantee_user_id=(SELECT id FROM users WHERE email=?) "
                    "OR grantee_org_id=(SELECT id FROM organizations WHERE slug=?))",
                    (project_id, (user_email or "").lower(), org_slug))
        audit.for_principal(con, p, "project.unshared", entity_type="project", entity_id=project_id)


def revisions(con, p: Principal, project_id):
    _access(con, p, project_id)
    return [dict(r) for r in con.execute(
        "SELECT revision, engine_version, note, created_by, created_utc FROM project_revisions WHERE project_id=? "
        "ORDER BY revision DESC", (project_id,))]


def get_revision(con, p: Principal, project_id, revision: int):
    _access(con, p, project_id)
    r = con.execute("SELECT * FROM project_revisions WHERE project_id=? AND revision=?", (project_id, revision)).fetchone()
    if not r:
        raise NotFound("Revision not found")
    return {**dict(r), "payload": json.loads(r["payload"])}


def import_legacy_projects(con, p: Principal):
    """Move v0.2 single-user projects into the caller's organisation (once)."""
    p.require("org.manage")
    if not con.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='legacy_projects'").fetchone():
        return {"imported": 0}
    n = 0
    with transaction(con):
        for r in con.execute("SELECT * FROM legacy_projects").fetchall():
            if con.execute("SELECT 1 FROM projects WHERE id=?", (r["id"],)).fetchone():
                continue
            con.execute("INSERT INTO projects (id,org_id,name,status,payload,revision,created_by,created_utc,updated_by,updated_utc)"
                        " VALUES (?,?,?,'draft',?,?,?,?,?,?)", (r["id"], p.org_id, r["name"], r["payload"], r["revision"],
                                                                p.user_id, r["updated_utc"], p.user_id, r["updated_utc"]))
            con.execute("INSERT INTO project_revisions VALUES (?,?,?,?,?,?,?)",
                        (r["id"], r["revision"], r["payload"], "0.2.0", "imported from v0.2", p.user_id, utcnow()))
            n += 1
        con.execute("DROP TABLE legacy_projects")
        audit.for_principal(con, p, "project.legacy_imported", detail={"count": n})
    return {"imported": n}
