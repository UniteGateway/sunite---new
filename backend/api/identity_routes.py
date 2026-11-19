"""HTTP routes for accounts, organisations and tenant-scoped projects (Stage 2).

Bearer tokens in the Authorization header (no cookies -> no CSRF surface). Service-layer
errors map to HTTP status codes in one place."""
from __future__ import annotations
import contextlib, threading, uuid
from fastapi import Header, HTTPException, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field
from backend.core.config import get_settings
from backend.core.ratelimit import TokenBucket
from backend.core.logging import request_id
from backend.db.database import connect, migrate, transaction, utcnow
from backend.services import accounts as A, projects as P
from backend.services.context import Principal
from backend.services.errors import ServiceError, RateLimited, Unauthorized
from backend.auth.rbac import permissions_for

_auth_bucket = TokenBucket(capacity=20, refill_per_sec=20 / 60)   # 20 auth calls / minute / IP
_migrated, _mlock = set(), threading.Lock()


@contextlib.contextmanager
def db():
    s = get_settings()
    con = connect(s.db_path)
    try:
        with _mlock:
            if s.db_path not in _migrated:
                migrate(con); _migrated.add(s.db_path)
        yield con
    finally:
        con.close()


def _ip(request: Request | None):
    return getattr(getattr(request, "client", None), "host", None)


def _limit(request):
    if not _auth_bucket.allow(_ip(request) or "unknown"):
        raise HTTPException(429, "Too many requests; slow down", headers={"Retry-After": "60"})


def _bearer(authorization):
    if authorization and authorization.lower().startswith("bearer "):
        return authorization[7:].strip()
    return None


def principal(con, authorization, request=None) -> Principal:
    s = get_settings()
    if s.auth_mode == "local_demo" and not _bearer(authorization):
        return _demo_principal(con)
    return A.authenticate(con, s, _bearer(authorization), ip=_ip(request))


def _demo_principal(con):
    """v0.2-style single-operator mode for local use only (refused when SUNITE_ENV=production)."""
    row = con.execute("SELECT u.id uid, m.org_id FROM users u JOIN memberships m ON m.user_id=u.id WHERE u.email='local@demo.invalid'").fetchone()
    if not row:
        with transaction(con):
            oid, uid, now = str(uuid.uuid4()), str(uuid.uuid4()), utcnow()
            con.execute("INSERT INTO organizations (id,name,slug,kind,created_utc) VALUES (?,?,?,?,?)", (oid, "Local demo", "local-demo", "company", now))
            con.execute("INSERT INTO users (id,email,full_name,password_hash,email_verified_utc,created_utc,password_changed_utc) VALUES (?,?,?,?,?,?,?)",
                        (uid, "local@demo.invalid", "Local operator", "!disabled", now, now, now))
            con.execute("INSERT INTO memberships VALUES (?,?,?,?)", (uid, oid, "company_admin", now))
        row = {"uid": uid, "org_id": oid}
    return Principal(user_id=row["uid"], org_id=row["org_id"], role="company_admin",
                     permissions=frozenset(permissions_for("company_admin")))


def run(fn):
    """Execute a service call and translate ServiceError -> HTTPException."""
    try:
        return fn()
    except ServiceError as e:
        headers = {"WWW-Authenticate": "Bearer"} if isinstance(e, Unauthorized) else None
        raise HTTPException(e.status, e.message, headers=headers)


class RegisterIn(BaseModel):
    org_name: str = Field(max_length=120)
    full_name: str = Field(max_length=120)
    email: str = Field(max_length=254)
    password: str = Field(max_length=256)

class LoginIn(BaseModel):
    email: str = Field(max_length=254)
    password: str = Field(max_length=256)
    org_id: str | None = None

class TokenIn(BaseModel):
    token: str = Field(max_length=200)

class ResetIn(BaseModel):
    token: str = Field(max_length=200)
    password: str = Field(max_length=256)

class EmailIn(BaseModel):
    email: str = Field(max_length=254)

class PasswordChangeIn(BaseModel):
    current_password: str = Field(max_length=256)
    new_password: str = Field(max_length=256)

class InviteIn(BaseModel):
    email: str = Field(max_length=254)
    full_name: str = Field(default="", max_length=120)
    role: str

class RoleIn(BaseModel):
    role: str

class OrgSwitchIn(BaseModel):
    org_id: str

class ProjectIn(BaseModel):
    name: str = Field(max_length=200)
    payload: dict
    engine_version: str | None = None
    note: str | None = Field(default=None, max_length=500)

class StatusIn(BaseModel):
    status: str
    revision: int

class ShareIn(BaseModel):
    access: str = "read"
    user_email: str | None = None
    org_slug: str | None = None


def register(app):
    s = get_settings

    @app.post("/api/auth/register", status_code=201)
    def _register(body: RegisterIn, request: Request):
        _limit(request)
        with db() as con:
            return run(lambda: A.register_organization(con, s(), ip=_ip(request), **body.model_dump()))

    @app.post("/api/auth/verify-email")
    def _verify(body: TokenIn, request: Request):
        _limit(request)
        with db() as con:
            run(lambda: A.verify_email(con, body.token)); return {"verified": True}

    @app.post("/api/auth/login")
    def _login(body: LoginIn, request: Request, user_agent: str | None = Header(default=None)):
        _limit(request)
        with db() as con:
            return run(lambda: A.login(con, s(), email=body.email, password=body.password, org_id=body.org_id,
                                       ip=_ip(request), user_agent=user_agent))

    @app.post("/api/auth/logout")
    def _logout(request: Request, everywhere: bool = False, authorization: str | None = Header(default=None)):
        with db() as con:
            run(lambda: A.logout(con, principal(con, authorization, request), everywhere)); return {"ok": True}

    @app.post("/api/auth/password-reset/request")
    def _reset_req(body: EmailIn, request: Request):
        _limit(request)
        with db() as con:
            run(lambda: A.request_password_reset(con, s(), body.email, ip=_ip(request)))
        return {"ok": True, "message": "If an account exists for that email, a reset link has been sent."}

    @app.post("/api/auth/password-reset/confirm")
    def _reset(body: ResetIn, request: Request):
        _limit(request)
        with db() as con:
            run(lambda: A.reset_password(con, body.token, body.password, ip=_ip(request))); return {"ok": True}

    @app.post("/api/auth/accept-invite")
    def _accept(body: ResetIn, request: Request):
        _limit(request)
        with db() as con:
            run(lambda: A.accept_invite(con, body.token, body.password, ip=_ip(request))); return {"ok": True}

    @app.post("/api/auth/password")
    def _change(body: PasswordChangeIn, request: Request, authorization: str | None = Header(default=None)):
        with db() as con:
            run(lambda: A.change_password(con, principal(con, authorization, request), body.current_password, body.new_password))
        return {"ok": True, "message": "Password changed; please sign in again on all devices."}

    @app.get("/api/me")
    def _me(request: Request, authorization: str | None = Header(default=None)):
        with db() as con:
            p = run(lambda: principal(con, authorization, request))
            u = con.execute("SELECT email, full_name FROM users WHERE id=?", (p.user_id,)).fetchone()
            return {"user_id": p.user_id, "email": u["email"], "full_name": u["full_name"], "org_id": p.org_id,
                    "role": p.role, "permissions": sorted(p.permissions),
                    "organizations": A.my_organizations(con, p)}

    @app.post("/api/auth/switch-org")
    def _switch(body: OrgSwitchIn, request: Request, authorization: str | None = Header(default=None)):
        with db() as con:
            return run(lambda: A.switch_org(con, s(), principal(con, authorization, request), body.org_id, ip=_ip(request)))

    @app.get("/api/org/members")
    def _members(request: Request, authorization: str | None = Header(default=None)):
        with db() as con:
            return run(lambda: A.list_members(con, principal(con, authorization, request)))

    @app.post("/api/org/members", status_code=201)
    def _invite(body: InviteIn, request: Request, authorization: str | None = Header(default=None)):
        with db() as con:
            return run(lambda: A.invite_member(con, s(), principal(con, authorization, request), **body.model_dump()))

    @app.put("/api/org/members/{user_id}/role")
    def _role(user_id: str, body: RoleIn, request: Request, authorization: str | None = Header(default=None)):
        with db() as con:
            run(lambda: A.set_role(con, principal(con, authorization, request), user_id, body.role)); return {"ok": True}

    @app.delete("/api/org/members/{user_id}")
    def _remove(user_id: str, request: Request, authorization: str | None = Header(default=None)):
        with db() as con:
            run(lambda: A.remove_member(con, principal(con, authorization, request), user_id)); return {"ok": True}

    @app.get("/api/org/audit")
    def _audit(request: Request, limit: int = 200, authorization: str | None = Header(default=None)):
        with db() as con:
            return run(lambda: A.audit_log(con, principal(con, authorization, request), limit))

    # ---- tenant-scoped projects (v0.3 store) ----
    @app.get("/api/v3/projects")
    def _list(request: Request, authorization: str | None = Header(default=None)):
        with db() as con:
            return run(lambda: P.list_projects(con, principal(con, authorization, request)))

    @app.post("/api/v3/projects", status_code=201)
    def _create(body: ProjectIn, request: Request, authorization: str | None = Header(default=None)):
        with db() as con:
            return run(lambda: P.create(con, principal(con, authorization, request), name=body.name,
                                        payload=body.payload, engine_version=body.engine_version))

    @app.get("/api/v3/projects/{pid}")
    def _get(pid: str, request: Request, authorization: str | None = Header(default=None)):
        with db() as con:
            return run(lambda: P.get(con, principal(con, authorization, request), pid))

    @app.put("/api/v3/projects/{pid}")
    def _update(pid: str, body: ProjectIn, request: Request, if_match: int | None = Header(default=None),
                authorization: str | None = Header(default=None)):
        with db() as con:
            return run(lambda: P.update(con, principal(con, authorization, request), pid, payload=body.payload,
                                        if_match=if_match, name=body.name, note=body.note, engine_version=body.engine_version))

    @app.post("/api/v3/projects/{pid}/status")
    def _status(pid: str, body: StatusIn, request: Request, authorization: str | None = Header(default=None)):
        with db() as con:
            run(lambda: P.set_status(con, principal(con, authorization, request), pid, body.status, body.revision))
            return {"ok": True}

    @app.delete("/api/v3/projects/{pid}")
    def _delete(pid: str, request: Request, authorization: str | None = Header(default=None)):
        with db() as con:
            run(lambda: P.delete(con, principal(con, authorization, request), pid)); return {"deleted": True}

    @app.post("/api/v3/projects/{pid}/share")
    def _share(pid: str, body: ShareIn, request: Request, authorization: str | None = Header(default=None)):
        with db() as con:
            run(lambda: P.share(con, principal(con, authorization, request), pid, **body.model_dump())); return {"ok": True}

    @app.get("/api/v3/projects/{pid}/revisions")
    def _revs(pid: str, request: Request, authorization: str | None = Header(default=None)):
        with db() as con:
            return run(lambda: P.revisions(con, principal(con, authorization, request), pid))

    @app.get("/api/v3/projects/{pid}/revisions/{rev}")
    def _rev(pid: str, rev: int, request: Request, authorization: str | None = Header(default=None)):
        with db() as con:
            return run(lambda: P.get_revision(con, principal(con, authorization, request), pid, rev))

    @app.post("/api/org/import-legacy-projects")
    def _import(request: Request, authorization: str | None = Header(default=None)):
        with db() as con:
            return run(lambda: P.import_legacy_projects(con, principal(con, authorization, request)))
