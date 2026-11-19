"""Connection management and a checksum-verified migration runner.

Migrations live in ./migrations as NNNN_name.sql or NNNN_name.py (with upgrade(con)).
Each runs in its own transaction; applied checksums are recorded so an edited,
already-applied migration is detected and refused instead of silently diverging."""
from __future__ import annotations
import contextlib, hashlib, importlib.util, os, sqlite3
from datetime import datetime, timezone

MIG_DIR = os.path.join(os.path.dirname(__file__), "migrations")


def utcnow() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def connect(path: str) -> sqlite3.Connection:
    con = sqlite3.connect(path, isolation_level=None, timeout=10)  # explicit transactions
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA foreign_keys=ON")
    con.execute("PRAGMA journal_mode=WAL") if path != ":memory:" else None
    return con


@contextlib.contextmanager
def transaction(con: sqlite3.Connection):
    con.execute("BEGIN IMMEDIATE")
    try:
        yield con
        con.execute("COMMIT")
    except BaseException:
        con.execute("ROLLBACK")
        raise


class MigrationError(RuntimeError):
    pass


def _files():
    out = []
    for f in sorted(os.listdir(MIG_DIR)):
        if f[:4].isdigit() and f.endswith((".sql", ".py")):
            data = open(os.path.join(MIG_DIR, f), "rb").read()
            out.append((f.rsplit(".", 1)[0], f, hashlib.sha256(data).hexdigest()))
    return out


def migrate(con: sqlite3.Connection) -> list[str]:
    con.execute("CREATE TABLE IF NOT EXISTS schema_migrations (version TEXT PRIMARY KEY, checksum TEXT NOT NULL, applied_utc TEXT NOT NULL)")
    done = {r["version"]: r["checksum"] for r in con.execute("SELECT version, checksum FROM schema_migrations")}
    applied = []
    for version, fname, checksum in _files():
        if version in done:
            if done[version] != checksum:
                raise MigrationError(f"Applied migration {fname} was modified after it ran; create a new migration instead")
            continue
        path = os.path.join(MIG_DIR, fname)
        with transaction(con):
            if fname.endswith(".sql"):
                sql = "\n".join(l.split("--", 1)[0] for l in open(path).read().splitlines())  # drop comments
                for stmt in [s.strip() for s in sql.split(";") if s.strip()]:
                    con.execute(stmt)
            else:
                spec = importlib.util.spec_from_file_location(f"mig_{version}", path)
                mod = importlib.util.module_from_spec(spec)
                spec.loader.exec_module(mod)
                _run_py(con, mod)
            con.execute("INSERT INTO schema_migrations VALUES (?,?,?)", (version, checksum, utcnow()))
        applied.append(version)
    return applied


def _run_py(con, mod):
    # executescript() would COMMIT our transaction; split statements and run them individually instead.
    class _Proxy:
        def __init__(self, c): self._c = c
        def execute(self, *a): return self._c.execute(*a)
        def executescript(self, script):
            for stmt in [s.strip() for s in script.split(";") if s.strip()]:
                self._c.execute(stmt)
    mod.upgrade(_Proxy(con))
