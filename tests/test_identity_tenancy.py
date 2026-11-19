"""Stage 1-2 tests: migrations, accounts, sessions, RBAC and tenant isolation (stdlib + sqlite only)."""
import os, re, shutil, sqlite3, tempfile
from backend.core.config import Settings
from backend.db import database as dbm
from backend.db.database import connect, migrate, MigrationError
from backend.services import accounts as A, projects as P
from backend.services.errors import Unauthorized, Forbidden, NotFound, Conflict, RateLimited, ValidationFailed
from backend.auth.passwords import hash_password, verify_password
from backend.auth.rbac import ROLES

S = Settings(max_failed_logins=3, lockout_window_min=15)
PW = "Correct-Horse-42"


def _raises(exc, fn, *a, **k):
    try:
        fn(*a, **k)
    except exc:
        return True
    except Exception as e:
        raise AssertionError(f"expected {exc.__name__}, got {type(e).__name__}: {e}")
    raise AssertionError(f"expected {exc.__name__}, nothing raised")


def _db():
    con = connect(":memory:")
    migrate(con)
    return con


def _last_token(con, email, kind):
    body = con.execute("SELECT body FROM email_outbox WHERE to_email=? AND template=? ORDER BY rowid DESC LIMIT 1",
                       (email, kind)).fetchone()["body"]
    return re.search(r"token=([\w\-]+)", body).group(1)


def _company(con, org, email):
    A.register_organization(con, S, org_name=org, full_name="Admin " + org, email=email, password=PW)
    A.verify_email(con, _last_token(con, email, "verify_email"))
    tok = A.login(con, S, email=email, password=PW)["token"]
    return A.authenticate(con, S, tok), tok


def _member(con, admin, email, role):
    A.invite_member(con, S, admin, email=email, full_name=email, role=role)
    A.accept_invite(con, _last_token(con, email, "invite"), PW)
    return A.authenticate(con, S, A.login(con, S, email=email, password=PW)["token"])


# ---------------- migrations ----------------
def test_migrations_idempotent_and_tamper_detected():
    con = _db()
    assert migrate(con) == []
    tmp = tempfile.mkdtemp()
    try:
        for f in os.listdir(dbm.MIG_DIR):
            shutil.copy(os.path.join(dbm.MIG_DIR, f), tmp) if os.path.isfile(os.path.join(dbm.MIG_DIR, f)) else None
        orig = dbm.MIG_DIR
        dbm.MIG_DIR = tmp
        c2 = connect(":memory:"); migrate(c2)
        with open(os.path.join(tmp, "0001_core_identity.sql"), "a") as fh:
            fh.write("\n-- edited after release\n")
        assert _raises(MigrationError, migrate, c2)
    finally:
        dbm.MIG_DIR = orig
        shutil.rmtree(tmp)


def test_v02_projects_preserved_and_importable():
    path = os.path.join(tempfile.mkdtemp(), "legacy.sqlite3")
    old = sqlite3.connect(path)
    old.execute("CREATE TABLE projects (id TEXT PRIMARY KEY, name TEXT NOT NULL, payload TEXT NOT NULL, revision INTEGER NOT NULL, updated_utc TEXT NOT NULL)")
    old.execute("INSERT INTO projects VALUES ('legacy-1','Old roof','{\"roof\":[]}',3,'2026-10-01T00:00:00+00:00')")
    old.commit(); old.close()
    con = connect(path); migrate(con)
    admin, _ = _company(con, "Unite Solar", "ravi@unite.example")
    assert P.import_legacy_projects(con, admin) == {"imported": 1}
    got = P.get(con, admin, "legacy-1")
    assert got["revision"] == 3 and got["org_id"] == admin.org_id and got["payload"] == {"roof": []}


# ---------------- accounts ----------------
def test_password_hashing():
    h = hash_password(PW)
    assert h.startswith("scrypt$") and PW not in h
    assert verify_password(PW, h) and not verify_password(PW + "x", h)
    assert hash_password(PW) != h                               # salted
    assert not verify_password(PW, "garbage")


def test_registration_verification_and_policy():
    con = _db()
    assert _raises(ValidationFailed, A.register_organization, con, S, org_name="X", full_name="Y", email="bad", password=PW)
    assert _raises(ValidationFailed, A.register_organization, con, S, org_name="X", full_name="Y", email="a@b.co", password="short")
    assert _raises(ValidationFailed, A.register_organization, con, S, org_name="X", full_name="Y", email="a@b.co", password="password123")
    A.register_organization(con, S, org_name="Acme", full_name="A", email="Admin@Acme.example", password=PW)
    assert _raises(Conflict, A.register_organization, con, S, org_name="Acme2", full_name="A", email="admin@acme.example", password=PW)
    assert _raises(Forbidden, A.login, con, S, email="admin@acme.example", password=PW)          # not verified
    tok = _last_token(con, "admin@acme.example", "verify_email")
    A.verify_email(con, tok)
    assert _raises(ValidationFailed, A.verify_email, con, tok)                                   # single use
    assert A.login(con, S, email="ADMIN@acme.example", password=PW)["role"] == "company_admin"
    raw = con.execute("SELECT token_hash FROM sessions").fetchone()[0]
    assert len(raw) == 64                                                                         # only digests stored


def test_login_lockout_and_no_enumeration():
    con = _db()
    _company(con, "Acme", "admin@acme.example")
    for _ in range(3):
        assert _raises(Unauthorized, A.login, con, S, email="admin@acme.example", password="wrong-password")
    assert _raises(RateLimited, A.login, con, S, email="admin@acme.example", password=PW)
    # unknown account behaves exactly like a wrong password
    e = None
    try:
        A.login(con, S, email="nobody@acme.example", password=PW)
    except Unauthorized as x:
        e = x
    assert e and e.message == "Incorrect email or password"
    A.request_password_reset(con, S, "nobody@acme.example")                                      # silent
    assert con.execute("SELECT COUNT(*) FROM email_outbox WHERE to_email='nobody@acme.example'").fetchone()[0] == 0


def test_sessions_expiry_idle_logout():
    con = _db()
    p, tok = _company(con, "Acme", "admin@acme.example")
    assert _raises(Unauthorized, A.authenticate, con, S, "not-a-token")
    con.execute("UPDATE sessions SET last_seen_utc='2000-01-01T00:00:00+00:00'")
    assert _raises(Unauthorized, A.authenticate, con, S, tok)                                    # idle timeout
    tok = A.login(con, S, email="admin@acme.example", password=PW)["token"]
    con.execute("UPDATE sessions SET expires_utc='2000-01-01T00:00:00+00:00' WHERE revoked_utc IS NULL")
    assert _raises(Unauthorized, A.authenticate, con, S, tok)                                    # absolute expiry
    tok = A.login(con, S, email="admin@acme.example", password=PW)["token"]
    p = A.authenticate(con, S, tok)
    A.logout(con, p)
    assert _raises(Unauthorized, A.authenticate, con, S, tok)


def test_password_reset_revokes_sessions_and_is_single_use():
    con = _db()
    _, tok = _company(con, "Acme", "admin@acme.example")
    A.request_password_reset(con, S, "admin@acme.example")
    A.request_password_reset(con, S, "admin@acme.example")                                       # older link invalidated
    newest = _last_token(con, "admin@acme.example", "reset_password")
    A.reset_password(con, newest, "Brand-New-Pass-77")
    assert _raises(ValidationFailed, A.reset_password, con, newest, "Another-Pass-88")
    assert _raises(Unauthorized, A.authenticate, con, S, tok)                                    # all sessions revoked
    assert _raises(Unauthorized, A.login, con, S, email="admin@acme.example", password=PW)
    A.login(con, S, email="admin@acme.example", password="Brand-New-Pass-77")
    con.execute("UPDATE one_time_tokens SET used_utc=NULL, expires_utc='2000-01-01T00:00:00+00:00'")
    assert _raises(ValidationFailed, A.reset_password, con, newest, "Third-Pass-999")             # expired


def test_last_admin_protected_and_role_changes_audited():
    con = _db()
    admin, _ = _company(con, "Acme", "admin@acme.example")
    assert _raises(Conflict, A.set_role, con, admin, admin.user_id, "solar_designer")
    assert _raises(Conflict, A.remove_member, con, admin, admin.user_id)
    d = _member(con, admin, "d@acme.example", "solar_designer")
    assert _raises(Forbidden, A.invite_member, con, S, d, email="x@acme.example", full_name="x", role="customer")
    assert _raises(ValidationFailed, A.invite_member, con, S, admin, email="x@acme.example", full_name="x", role="god")
    A.set_role(con, admin, d.user_id, "company_admin")
    A.set_role(con, admin, admin.user_id, "project_manager")                                     # allowed now
    actions = [r["action"] for r in con.execute("SELECT action FROM audit_logs")]
    assert actions.count("org.role_changed") == 2 and "org.member_invited" in actions
    assert all("password" not in (r["detail"] or "") for r in con.execute("SELECT detail FROM audit_logs"))


def test_removed_member_loses_access_immediately():
    con = _db()
    admin, _ = _company(con, "Acme", "admin@acme.example")
    tok = (A.invite_member(con, S, admin, email="d@acme.example", full_name="D", role="solar_designer"),
           A.accept_invite(con, _last_token(con, "d@acme.example", "invite"), PW),
           A.login(con, S, email="d@acme.example", password=PW)["token"])[2]
    d = A.authenticate(con, S, tok)
    A.remove_member(con, admin, d.user_id)
    assert _raises(Unauthorized, A.authenticate, con, S, tok)


# ---------------- RBAC ----------------
def test_role_matrix():
    assert "design.electrical.approve" not in ROLES["company_admin"]      # sign-off belongs to engineers
    assert "design.electrical.approve" in ROLES["electrical_engineer"]
    assert ROLES["customer"] == {"project.read"}
    con = _db()
    admin, _ = _company(con, "Acme", "admin@acme.example")
    sales = _member(con, admin, "s@acme.example", "sales_executive")
    pid = P.create(con, sales, name="Lead roof", payload={"roof": []})["id"]
    assert _raises(Forbidden, P.update, con, sales, pid, payload={}, if_match=1)    # sales cannot edit design
    assert _raises(Forbidden, P.delete, con, sales, pid)
    pm = _member(con, admin, "pm@acme.example", "project_manager")
    designer = _member(con, admin, "d@acme.example", "solar_designer")
    assert _raises(Forbidden, P.set_status, con, designer, pid, "approved", 1)
    P.set_status(con, pm, pid, "approved", 1)
    assert _raises(Conflict, P.update, con, designer, pid, payload={}, if_match=1)  # approved = locked
    P.set_status(con, pm, pid, "draft", 1)
    assert P.update(con, designer, pid, payload={"v": 2}, if_match=1)["revision"] == 2


# ---------------- tenant isolation (acceptance criterion of Stage 2) ----------------
def test_tenant_isolation():
    con = _db()
    a, _ = _company(con, "Unite Solar", "ravi@unite.example")
    b, _ = _company(con, "Other EPC", "boss@other.example")
    pid = P.create(con, a, name="Confidential roof", payload={"secret": 1})["id"]
    for fn, args in [(P.get, ()), (P.revisions, ()), (P.delete, ()), (P.get_revision, (1,))]:
        assert _raises(NotFound, fn, con, b, pid, *args)                            # NotFound, not Forbidden
    assert _raises(NotFound, P.update, con, b, pid, payload={}, if_match=1)
    assert _raises(NotFound, P.share, con, b, pid, access="edit", org_slug="other-epc")
    assert P.list_projects(con, b) == []
    assert [r["id"] for r in P.list_projects(con, a)] == [pid]
    assert _raises(Forbidden, A.audit_log, con, _member(con, b, "x@other.example", "solar_designer"))
    assert all(r["org_id"] == b.org_id for r in A.audit_log(con, b))


def test_explicit_sharing_and_revocation():
    con = _db()
    a, _ = _company(con, "Unite Solar", "ravi@unite.example")
    b, _ = _company(con, "Franchise One", "fr@franchise.example")
    pid = P.create(con, a, name="Shared roof", payload={"x": 1})["id"]
    P.share(con, a, pid, access="read", org_slug="franchise-one")
    got = P.get(con, b, pid)
    assert got["access"] == "read" and [r["shared"] for r in P.list_projects(con, b)] == [True]
    assert _raises(Forbidden, P.update, con, b, pid, payload={}, if_match=1)
    P.share(con, a, pid, access="edit", org_slug="franchise-one")
    assert P.update(con, b, pid, payload={"x": 2}, if_match=1)["revision"] == 2
    assert _raises(NotFound, P.delete, con, b, pid)                                  # only owner deletes
    assert _raises(Forbidden, P.share, con, b, pid, access="edit", user_email="ravi@unite.example")
    P.revoke_share(con, a, pid, org_slug="franchise-one")
    assert _raises(NotFound, P.get, con, b, pid)


def test_customer_sees_only_granted_projects():
    con = _db()
    a, _ = _company(con, "Unite Solar", "ravi@unite.example")
    cust = _member(con, a, "client@home.example", "customer")
    p1 = P.create(con, a, name="Client house", payload={})["id"]
    P.create(con, a, name="Other client", payload={})
    assert P.list_projects(con, cust) == []
    assert _raises(NotFound, P.get, con, cust, p1)
    P.share(con, a, p1, access="edit", user_email="client@home.example")
    assert [r["id"] for r in P.list_projects(con, cust)] == [p1]
    assert P.get(con, cust, p1)["access"] == "read"                                  # role caps grant at read


def test_revisions_and_conflicts():
    con = _db()
    a, _ = _company(con, "Acme", "admin@acme.example")
    pid = P.create(con, a, name="R", payload={"v": 1}, engine_version="0.3.0")["id"]
    assert _raises(Conflict, P.update, con, a, pid, payload={"v": 2}, if_match=None)
    assert _raises(Conflict, P.update, con, a, pid, payload={"v": 2}, if_match=7)
    P.update(con, a, pid, payload={"v": 2}, if_match=1, note="moved inverter")
    assert [r["revision"] for r in P.revisions(con, a, pid)] == [2, 1]
    assert P.get_revision(con, a, pid, 1)["payload"] == {"v": 1}
    assert _raises(ValidationFailed, P.create, con, a, name="big", payload={"x": "y" * 2_100_000})


def test_switch_org_for_multi_company_users():
    con = _db()
    a, _ = _company(con, "Unite Solar", "ravi@unite.example")
    b, _ = _company(con, "Unite Bloomlight", "ops@bloom.example")
    A.invite_member(con, S, b, email="ravi@unite.example", full_name="Ravi", role="project_manager")
    orgs = A.my_organizations(con, a)
    assert len(orgs) == 2
    tok = A.login(con, S, email="ravi@unite.example", password=PW)["token"]
    p = A.authenticate(con, S, tok)
    new = A.switch_org(con, S, p, b.org_id)
    assert _raises(Unauthorized, A.authenticate, con, S, tok)
    assert A.authenticate(con, S, new["token"]).role == "project_manager"
    stranger, _ = _company(con, "Stranger Co", "s@stranger.example")
    assert _raises(Forbidden, A.switch_org, con, S, p, stranger.org_id)


def test_token_bucket():
    from backend.core.ratelimit import TokenBucket
    t = [0.0]
    b = TokenBucket(capacity=3, refill_per_sec=1, clock=lambda: t[0])
    assert [b.allow("ip") for _ in range(4)] == [True, True, True, False]
    assert b.allow("other")                      # per-key
    t[0] += 1.0
    assert b.allow("ip") and not b.allow("ip")


def test_production_refuses_demo_mode():
    from backend.core.config import Settings
    assert _raises(RuntimeError, Settings(env="production", auth_mode="local_demo").validate)
    Settings(env="development", auth_mode="local_demo").validate()
