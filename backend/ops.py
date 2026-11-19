"""Operational safety (Stage 22): consistent online backups with integrity check + checksum, verified restore,
and validation of text uploads (DXF / GeoJSON / CSV) before parsing."""
from __future__ import annotations
import hashlib, os, shutil, sqlite3
from datetime import datetime, timezone


def _sha(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def backup(db_path: str, out_dir: str, keep: int = 14) -> dict:
    os.makedirs(out_dir, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    dst = os.path.join(out_dir, f"sunite-{stamp}.sqlite3")
    src = sqlite3.connect(db_path); out = sqlite3.connect(dst)
    with out:
        src.backup(out)                                  # consistent snapshot while the app runs
    ok = out.execute("PRAGMA integrity_check").fetchone()[0]
    src.close(); out.close()
    if ok != "ok":
        os.remove(dst)
        raise RuntimeError(f"Backup failed integrity check: {ok}")
    digest = _sha(dst)
    open(dst + ".sha256", "w").write(digest)
    olds = sorted(f for f in os.listdir(out_dir) if f.endswith(".sqlite3"))
    for f in olds[:-keep]:
        os.remove(os.path.join(out_dir, f))
        if os.path.exists(os.path.join(out_dir, f + ".sha256")):
            os.remove(os.path.join(out_dir, f + ".sha256"))
    return {"path": dst, "sha256": digest, "integrity": ok}


def restore(backup_path: str, db_path: str, expected_sha256: str | None = None) -> None:
    expected = expected_sha256 or (open(backup_path + ".sha256").read().strip() if os.path.exists(backup_path + ".sha256") else None)
    if not expected or _sha(backup_path) != expected:
        raise ValueError("Backup checksum missing or does not match: refusing to restore")
    chk = sqlite3.connect(backup_path)
    ok = chk.execute("PRAGMA integrity_check").fetchone()[0]; chk.close()
    if ok != "ok":
        raise ValueError(f"Backup integrity check failed: {ok}")
    if os.path.exists(db_path):
        shutil.copy2(db_path, db_path + ".pre-restore")
    for ext in ("-wal", "-shm"):
        if os.path.exists(db_path + ext):
            os.remove(db_path + ext)
    shutil.copy2(backup_path, db_path)


def validate_text_upload(text: str, *, kind: str, max_bytes: int) -> None:
    if not isinstance(text, str):
        raise ValueError("Upload must be text")
    if len(text.encode("utf-8", "ignore")) > max_bytes:
        raise ValueError(f"{kind.upper()} upload exceeds {max_bytes // 1_000_000 or max_bytes} {'MB' if max_bytes >= 1_000_000 else 'bytes'}")
    if "\x00" in text:
        raise ValueError("Binary content is not accepted; export as ASCII DXF / UTF-8 text")
    head = text.lstrip()[:200].lower()
    if head.startswith(("<html", "<!doctype", "<?php", "<script")):
        raise ValueError("File does not look like a " + kind.upper())
    if kind == "dxf" and "section" not in text[:5000].lower():
        raise ValueError("Not an ASCII DXF (no SECTION found)")
    return None
