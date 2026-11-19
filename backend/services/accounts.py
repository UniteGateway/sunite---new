"""Registration, email verification, login/sessions, password reset, invitations, memberships."""
from __future__ import annotations
import re, uuid
from datetime import datetime, timedelta, timezone
from backend.core.config import Settings
from backend.db.database import transaction, utcnow
from backend.auth.passwords import hash_password, verify_password, check_policy, WeakPassword, DUMMY_HASH
from backend.auth.tokens import new_token, digest
from backend.auth.rbac import permissions_for, ROLE_NAMES
from .context import Principal
from .errors import ValidationFailed, Unauthorized, NotFound, Conflict, RateLimited, Forbidden
from . import audit

EMAIL_RE = re.compile(r"^[^@\s]{1,64}@[^@\s]+\.[^@\s]{2,}$")


def _now():
    return datetime.now(timezone.utc)


def _iso(dt):
    return dt.isoformat(timespec="seconds")


def _email(e: str) -> str:
    e = (e or "").strip().lower()
    if not EMAIL_RE.match(e) or len(e) > 254:
        raise ValidationFailed("Invalid email address")
    return e


def _slug(con, name):
    base = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")[:40] or "org"
    slug, i = base, 1
    while con.execute("SELECT 1 FROM organizations WHERE slug=?", (slug,)).fetchone():
        i += 1; slug = f"{base}-{i}"
    return slug


def _queue_email(con, to, subject, body, template):
    con.execute("INSERT INTO email_outbox (id,to_email,subject,body,template,created_utc) VALUES (?,?,?,?,?,?)",
                (str(uuid.uuid4()), to, subject, body, template, utcnow()))


def _issue_token(con, user_id, purpose, ttl_min):
    raw, h = new_token()
    con.execute("INSERT INTO one_time_tokens VALUES (?,?,?,?,NULL,?)",
                (h, user_id, purpose, _iso(_now() + timedelta(minutes=ttl_min)), utcnow()))
    return raw


def _consume_token(con, raw, purpose):
    row = con.execute("SELECT * FROM one_time_tokens WHERE token_hash=? AND purpose=?", (digest(raw or ""), purpose)).fetchone()
    if not row or row["used_utc"] or row["expires_utc"] < _iso(_now()):
        raise ValidationFailed("This link is invalid or has expired")
    con.execute("UPDATE one_time_tokens SET used_utc=? WHERE token_hash=?", (utcnow(), row["token_hash"]))
    return row["user_id"]


def _policy(pw, email, name=""):
    try:
        check_policy(pw, email, name)
    except WeakPassword as e:
        raise ValidationFailed(str(e))


def register_organization(con, s: Settings, *, org_name, full_name, email, password, ip=None):
    email = _email(email)
    if not org_name.strip() or not full_name.strip():
        raise ValidationFailed("Organisation and full name are required")
    _policy(password, email, full_name)
    with transaction(con):
        if con.execute("SELECT 1 FROM users WHERE email=?", (email,)).fetchone():
            raise Conflict("An account already exists for this email; sign in or reset your password")
        org_id, user_id, now = str(uuid.uuid4()), str(uuid.uuid4()), utcnow()
        con.execute("INSERT INTO organizations (id,name,slug,kind,created_utc) VALUES (?,?,?,?,?)",
                    (org_id, org_name.strip()[:120], _slug(con, org_name), "company", now))
        con.execute("INSERT INTO users (id,email,full_name,password_hash,created_utc,password_changed_utc) VALUES (?,?,?,?,?,?)",
                    (user_id, email, full_name.strip()[:120], hash_password(password), now, now))
        con.execute("INSERT INTO memberships VALUES (?,?,?,?)", (user_id, org_id, "company_admin", now))
        raw = _issue_token(con, user_id, "verify_email", s.verify_token_ttl_min)
        _queue_email(con, email, "Verify your SUNITE account",
                     f"Verify your email: {s.app_base_url}/verify?token={raw}", "verify_email")
        audit.record(con, "org.registered", org_id=org_id, user_id=user_id, entity_type="organization",
                     entity_id=org_id, ip=ip)
    return {"org_id": org_id, "user_id": user_id}


def verify_email(con, raw_token):
    with transaction(con):
        uid = _consume_token(con, raw_token, "verify_email")
        con.execute("UPDATE users SET email_verified_utc=COALESCE(email_verified_utc, ?) WHERE id=?", (utcnow(), uid))
        audit.record(con, "user.email_verified", user_id=uid, entity_type="user", entity_id=uid)


def _locked(con, s, email):
    since = _iso(_now() - timedelta(minutes=s.lockout_window_min))
    last_ok = con.execute("SELECT MAX(at_utc) FROM login_attempts WHERE email=? AND success=1", (email,)).fetchone()[0]
    floor = max(since, last_ok or "")
    n = con.execute("SELECT COUNT(*) FROM login_attempts WHERE email=? AND success=0 AND at_utc>=?", (email, floor)).fetchone()[0]
    return n >= s.max_failed_logins


def login(con, s: Settings, *, email, password, org_id=None, ip=None, user_agent=None):
    try:
        email = _email(email)
    except ValidationFailed:
        raise Unauthorized("Incorrect email or password")
    if _locked(con, s, email):
        raise RateLimited(f"Too many failed sign-in attempts. Try again in {s.lockout_window_min} minutes or reset your password.")
    user = con.execute("SELECT * FROM users WHERE email=?", (email,)).fetchone()
    ok = verify_password(password or "", user["password_hash"] if user else DUMMY_HASH) and user is not None
    with transaction(con):   # committed on its own so a failed attempt is never rolled back
        con.execute("INSERT INTO login_attempts (email,ip,success,at_utc) VALUES (?,?,?,?)", (email, ip, int(ok), utcnow()))
        if not ok:
            audit.record(con, "auth.login_failed", user_id=user["id"] if user else None, ip=ip, detail={"email": email})
    if not ok or not user["is_active"]:
        raise Unauthorized("Incorrect email or password")
    if not user["email_verified_utc"]:
        raise Forbidden("Please verify your email address before signing in")
    with transaction(con):
        mem = _membership(con, user["id"], org_id)
        token = _create_session(con, s, user["id"], mem["org_id"], ip, user_agent)
        audit.record(con, "auth.login", org_id=mem["org_id"], user_id=user["id"], ip=ip)
    return {"token": token["raw"], "expires_utc": token["expires"], "org_id": mem["org_id"], "role": mem["role"]}


def _membership(con, user_id, org_id=None):
    q = "SELECT m.* FROM memberships m JOIN organizations o ON o.id=m.org_id WHERE m.user_id=? AND o.is_active=1"
    rows = con.execute(q + (" AND m.org_id=?" if org_id else " ORDER BY m.created_utc"),
                       (user_id, org_id) if org_id else (user_id,)).fetchall()
    if not rows:
        raise Forbidden("You are not a member of an active organisation" if not org_id else "Not a member of that organisation")
    return rows[0]


def _create_session(con, s, user_id, org_id, ip, ua):
    raw, h = new_token()
    now = _now()
    exp = _iso(now + timedelta(minutes=s.session_ttl_min))
    con.execute("INSERT INTO sessions VALUES (?,?,?,?,?,?,?,NULL,?,?)",
                (str(uuid.uuid4()), h, user_id, org_id, _iso(now), _iso(now), exp, (ua or "")[:200], ip))
    return {"raw": raw, "expires": exp}


def authenticate(con, s: Settings, raw_token, ip=None) -> Principal:
    if not raw_token:
        raise Unauthorized("Sign-in required")
    row = con.execute(
        "SELECT s.*, u.is_active, u.is_system_admin, m.role FROM sessions s JOIN users u ON u.id=s.user_id "
        "JOIN memberships m ON m.user_id=s.user_id AND m.org_id=s.org_id "
        "JOIN organizations o ON o.id=s.org_id AND o.is_active=1 WHERE s.token_hash=?", (digest(raw_token),)).fetchone()
    now = _now()
    if (not row or row["revoked_utc"] or row["expires_utc"] < _iso(now) or not row["is_active"]
            or row["last_seen_utc"] < _iso(now - timedelta(minutes=s.session_idle_min))):
        raise Unauthorized("Session expired; please sign in again")
    if row["last_seen_utc"] < _iso(now - timedelta(minutes=1)):  # throttle writes
        con.execute("UPDATE sessions SET last_seen_utc=? WHERE id=?", (_iso(now), row["id"]))
    return Principal(user_id=row["user_id"], org_id=row["org_id"], role=row["role"],
                     permissions=frozenset(permissions_for(row["role"])), session_id=row["id"],
                     is_system_admin=bool(row["is_system_admin"]), ip=ip)


def logout(con, p: Principal, everywhere=False):
    with transaction(con):
        if everywhere:
            con.execute("UPDATE sessions SET revoked_utc=? WHERE user_id=? AND revoked_utc IS NULL", (utcnow(), p.user_id))
        else:
            con.execute("UPDATE sessions SET revoked_utc=? WHERE id=?", (utcnow(), p.session_id))
        audit.for_principal(con, p, "auth.logout_all" if everywhere else "auth.logout")


def switch_org(con, s, p: Principal, org_id, ip=None, user_agent=None):
    with transaction(con):
        mem = _membership(con, p.user_id, org_id)
        con.execute("UPDATE sessions SET revoked_utc=? WHERE id=?", (utcnow(), p.session_id))
        tok = _create_session(con, s, p.user_id, mem["org_id"], ip, user_agent)
        audit.record(con, "auth.switch_org", org_id=org_id, user_id=p.user_id, ip=ip)
    return {"token": tok["raw"], "expires_utc": tok["expires"], "org_id": org_id, "role": mem["role"]}


def my_organizations(con, p: Principal):
    return [dict(r) for r in con.execute(
        "SELECT o.id, o.name, o.slug, o.kind, m.role FROM memberships m JOIN organizations o ON o.id=m.org_id "
        "WHERE m.user_id=? AND o.is_active=1 ORDER BY o.name", (p.user_id,))]


def request_password_reset(con, s, email, ip=None):
    """Always succeeds from the caller's view, so it cannot be used to discover accounts."""
    try:
        email = _email(email)
    except ValidationFailed:
        return
    with transaction(con):
        user = con.execute("SELECT id FROM users WHERE email=? AND is_active=1", (email,)).fetchone()
        if not user:
            return
        con.execute("UPDATE one_time_tokens SET used_utc=? WHERE user_id=? AND purpose='reset_password' AND used_utc IS NULL",
                    (utcnow(), user["id"]))
        raw = _issue_token(con, user["id"], "reset_password", s.reset_token_ttl_min)
        _queue_email(con, email, "Reset your SUNITE password",
                     f"Reset link (valid {s.reset_token_ttl_min} min): {s.app_base_url}/reset?token={raw}", "reset_password")
        audit.record(con, "auth.reset_requested", user_id=user["id"], ip=ip)


def _set_password(con, user_id, pw):
    con.execute("UPDATE users SET password_hash=?, password_changed_utc=? WHERE id=?", (hash_password(pw), utcnow(), user_id))
    con.execute("UPDATE sessions SET revoked_utc=? WHERE user_id=? AND revoked_utc IS NULL", (utcnow(), user_id))


def reset_password(con, raw_token, new_password, ip=None):
    with transaction(con):
        uid = _consume_token(con, raw_token, "reset_password")
        email = con.execute("SELECT email FROM users WHERE id=?", (uid,)).fetchone()["email"]
        _policy(new_password, email)
        _set_password(con, uid, new_password)
        # Reset proves mailbox control: also mark email verified.
        con.execute("UPDATE users SET email_verified_utc=COALESCE(email_verified_utc, ?) WHERE id=?", (utcnow(), uid))
        audit.record(con, "auth.password_reset", user_id=uid, ip=ip)


def change_password(con, p: Principal, old, new):
    with transaction(con):
        u = con.execute("SELECT * FROM users WHERE id=?", (p.user_id,)).fetchone()
        if not verify_password(old or "", u["password_hash"]):
            raise Unauthorized("Current password is incorrect")
        _policy(new, u["email"], u["full_name"])
        _set_password(con, p.user_id, new)
        audit.for_principal(con, p, "auth.password_changed")


# ---------------- organisation membership ----------------
def _check_role(role):
    if role not in ROLE_NAMES:
        raise ValidationFailed(f"Unknown role '{role}'. Valid: {', '.join(ROLE_NAMES)}")


def invite_member(con, s, p: Principal, *, email, full_name, role):
    p.require("org.members.manage")
    _check_role(role)
    email = _email(email)
    with transaction(con):
        user = con.execute("SELECT id FROM users WHERE email=?", (email,)).fetchone()
        now = utcnow()
        if user:
            if con.execute("SELECT 1 FROM memberships WHERE user_id=? AND org_id=?", (user["id"], p.org_id)).fetchone():
                raise Conflict("That person is already a member")
            uid = user["id"]
            _queue_email(con, email, "You were added to a SUNITE organisation",
                         f"You now have access as {role}. Sign in at {s.app_base_url}", "member_added")
        else:
            uid = str(uuid.uuid4())
            unusable = "!" + new_token()[1]   # cannot match any scrypt hash
            con.execute("INSERT INTO users (id,email,full_name,password_hash,created_utc,password_changed_utc) VALUES (?,?,?,?,?,?)",
                        (uid, email, (full_name or email).strip()[:120], unusable, now, now))
            raw = _issue_token(con, uid, "invite", s.verify_token_ttl_min)
            _queue_email(con, email, "You are invited to SUNITE AI Solar Designer",
                         f"Set your password: {s.app_base_url}/accept-invite?token={raw}", "invite")
        con.execute("INSERT INTO memberships VALUES (?,?,?,?)", (uid, p.org_id, role, now))
        audit.for_principal(con, p, "org.member_invited", entity_type="user", entity_id=uid, detail={"role": role})
    return {"user_id": uid}


def accept_invite(con, raw_token, password, ip=None):
    with transaction(con):
        uid = _consume_token(con, raw_token, "invite")
        email = con.execute("SELECT email FROM users WHERE id=?", (uid,)).fetchone()["email"]
        _policy(password, email)
        _set_password(con, uid, password)
        con.execute("UPDATE users SET email_verified_utc=COALESCE(email_verified_utc, ?) WHERE id=?", (utcnow(), uid))
        audit.record(con, "user.invite_accepted", user_id=uid, ip=ip)


def _admins_left(con, org_id, excluding):
    return con.execute("SELECT COUNT(*) FROM memberships WHERE org_id=? AND role='company_admin' AND user_id<>?",
                       (org_id, excluding)).fetchone()[0]


def set_role(con, p: Principal, user_id, role):
    p.require("org.members.manage")
    _check_role(role)
    with transaction(con):
        m = con.execute("SELECT role FROM memberships WHERE user_id=? AND org_id=?", (user_id, p.org_id)).fetchone()
        if not m:
            raise NotFound("Member not found")
        if m["role"] == "company_admin" and role != "company_admin" and _admins_left(con, p.org_id, user_id) == 0:
            raise Conflict("An organisation must keep at least one company admin")
        con.execute("UPDATE memberships SET role=? WHERE user_id=? AND org_id=?", (role, user_id, p.org_id))
        audit.for_principal(con, p, "org.role_changed", entity_type="user", entity_id=user_id,
                            detail={"from": m["role"], "to": role})


def remove_member(con, p: Principal, user_id):
    p.require("org.members.manage")
    with transaction(con):
        m = con.execute("SELECT role FROM memberships WHERE user_id=? AND org_id=?", (user_id, p.org_id)).fetchone()
        if not m:
            raise NotFound("Member not found")
        if m["role"] == "company_admin" and _admins_left(con, p.org_id, user_id) == 0:
            raise Conflict("An organisation must keep at least one company admin")
        con.execute("DELETE FROM memberships WHERE user_id=? AND org_id=?", (user_id, p.org_id))
        con.execute("UPDATE sessions SET revoked_utc=? WHERE user_id=? AND org_id=? AND revoked_utc IS NULL",
                    (utcnow(), user_id, p.org_id))
        audit.for_principal(con, p, "org.member_removed", entity_type="user", entity_id=user_id)


def list_members(con, p: Principal):
    p.require("project.read")
    return [dict(r) for r in con.execute(
        "SELECT u.id, u.email, u.full_name, m.role, u.email_verified_utc IS NOT NULL AS verified "
        "FROM memberships m JOIN users u ON u.id=m.user_id WHERE m.org_id=? ORDER BY u.full_name", (p.org_id,))]


def audit_log(con, p: Principal, limit=200):
    p.require("audit.read")
    return [dict(r) for r in con.execute("SELECT * FROM audit_logs WHERE org_id=? ORDER BY id DESC LIMIT ?",
                                         (p.org_id, min(limit, 1000)))]


def create_system_admin(con, *, email, full_name, password, org_name="SUNITE Platform"):
    """CLI bootstrap only (python -m backend.cli create-admin). Not exposed over HTTP."""
    email = _email(email)
    _policy(password, email, full_name)
    with transaction(con):
        now = utcnow()
        org_id, uid = str(uuid.uuid4()), str(uuid.uuid4())
        con.execute("INSERT INTO organizations (id,name,slug,kind,created_utc) VALUES (?,?,?,?,?)",
                    (org_id, org_name, _slug(con, org_name), "platform", now))
        con.execute("INSERT INTO users (id,email,full_name,password_hash,email_verified_utc,is_system_admin,created_utc,password_changed_utc)"
                    " VALUES (?,?,?,?,?,1,?,?)", (uid, email, full_name, hash_password(password), now, now, now))
        con.execute("INSERT INTO memberships VALUES (?,?,?,?)", (uid, org_id, "company_admin", now))
        audit.record(con, "system.admin_created", org_id=org_id, user_id=uid)
    return {"org_id": org_id, "user_id": uid}
