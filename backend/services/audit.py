import json
from backend.db.database import utcnow
from backend.core.logging import request_id

_SECRET_KEYS = ("password", "token", "secret")


def record(con, action, *, org_id=None, user_id=None, entity_type=None, entity_id=None, detail=None, ip=None):
    clean = {k: v for k, v in (detail or {}).items() if not any(s in k.lower() for s in _SECRET_KEYS)}
    con.execute("INSERT INTO audit_logs (at_utc, org_id, user_id, action, entity_type, entity_id, detail, ip, request_id)"
                " VALUES (?,?,?,?,?,?,?,?,?)", (utcnow(), org_id, user_id, action, entity_type, entity_id,
                                                json.dumps(clean, default=str), ip, request_id.get()))


def for_principal(con, p, action, **kw):
    record(con, action, org_id=p.org_id, user_id=p.user_id, ip=p.ip, **kw)
