"""Stage 10 protection/earthing/transformer and Stage 22 backup/restore + upload checks."""
import math, os, sqlite3, tempfile
from backend.engine.protection import string_protection, ac_breaker, earthing_conductor, transformer, protection_design
from backend.engine.electrical import design_strings
from tests.test_engine_electrical_finance import MOD, INV, T
from tests.test_identity_tenancy import _raises


def test_string_fuse_rule():
    assert string_protection(14.0, 25, 1)["required"] is False                 # single string: no reverse current
    assert string_protection(14.0, 25, 2)["required"] is False                 # 1 x 1.25 x 14 = 17.5 <= 25
    r = string_protection(14.0, 30, 3)                                         # 2 x 17.5 = 35 > 30
    assert r["required"] and r["window_a"] == [21.0, 30.0] and r["fuse_a"] == 25
    assert "error" in string_protection(14.0, 20, 4)                           # window 21..20 empty
    assert string_protection(14.0, None, 3)["required"] is None


def test_breaker_earthing_transformer():
    assert ac_breaker(83.5)["rating_a"] == 125                                 # 104.4 A -> 125 A
    e = earthing_conductor(10_000, 0.4, 143)                                   # sqrt(1e8*0.4)/143 = 44.2
    assert abs(e["min_area_mm2"] - math.sqrt(1e8 * 0.4) / 143) < 0.01 and e["size_mm2"] == 50
    assert transformer(220)["rating_kva"] == 315                               # 254.7 kVA -> 315


def test_protection_design_from_strings():
    el = design_strings(134, MOD, INV, T)
    p = protection_design(el, {"isc_a": 14.0, "max_series_fuse_a": 25}, grid_voltage_v=415, transformer_needed=True)
    i = 60_000 / (math.sqrt(3) * 415)
    assert abs(p["inverter_ac_current_a"] - i) < 0.01 and p["ac_breaker_per_inverter"]["rating_a"] == 125
    assert p["string_protection"]["required"] is False and p["transformer"]["rating_kva"] >= el["ac_kw"]
    assert p["earthing_conductor"]["status"] == "pending"


def test_backup_restore_roundtrip():
    from backend.db.database import connect, migrate
    from backend.ops import backup, restore, validate_text_upload
    d = tempfile.mkdtemp(); src = os.path.join(d, "live.sqlite3")
    con = connect(src); migrate(con)
    con.execute("INSERT INTO organizations (id,name,slug,kind,created_utc) VALUES ('o1','Org','org','company','2026')")
    info = backup(src, os.path.join(d, "backups"))
    assert info["integrity"] == "ok" and os.path.exists(info["path"]) and len(info["sha256"]) == 64
    con.execute("DELETE FROM organizations"); con.close()
    restore(info["path"], src, expected_sha256=info["sha256"])
    assert sqlite3.connect(src).execute("SELECT COUNT(*) FROM organizations").fetchone()[0] == 1
    open(info["path"], "ab").write(b"tamper")
    assert _raises(ValueError, restore, info["path"], src, expected_sha256=info["sha256"])
    assert validate_text_upload("0\nSECTION\n", kind="dxf", max_bytes=100) is None
    assert _raises(ValueError, validate_text_upload, "x" * 200, kind="dxf", max_bytes=100)
    assert _raises(ValueError, validate_text_upload, "abc\x00def", kind="csv", max_bytes=100)
    assert _raises(ValueError, validate_text_upload, "<html>", kind="dxf", max_bytes=100)
