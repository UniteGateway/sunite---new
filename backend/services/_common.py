import json, uuid
from .errors import NotFound


def new_id():
    return str(uuid.uuid4())


def get_scoped(con, table, row_id, org_id, label=None):
    r = con.execute(f"SELECT * FROM {table} WHERE id=? AND org_id=?", (row_id, org_id)).fetchone()
    if not r:
        raise NotFound(f"{label or table[:-1].replace('_', ' ').capitalize()} not found")
    return r


def j(v):
    return json.dumps(v, default=str)


def unj(v):
    return json.loads(v) if v else None
