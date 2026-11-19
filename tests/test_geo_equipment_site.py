"""Stage 3 (geo), Stage 9 (equipment catalogue) and multi-roof tests."""
import math, json
from backend.engine import geo, geometry as gm
from backend.engine.site import run_site
from backend.engine.pipeline import legacy_to_request
from backend.services import equipment as EQ, accounts as A
from backend.services.errors import ValidationFailed, Forbidden, Conflict, NotFound
from tests.test_identity_tenancy import _db, _company, _member, _raises, S, PW

# ---------------- geo ----------------
def test_utm_reference_values_and_roundtrip():
    u = geo.to_utm(0.0, 0.0)
    assert u["zone"] == 31 and abs(u["easting"] - 166021.4431) < 0.002 and abs(u["northing"]) < 1e-6
    u = geo.to_utm(0.0, 3.0)                                       # central meridian
    assert abs(u["easting"] - 500000) < 1e-6
    assert geo.to_utm(17.385, 78.487)["zone"] == 44 and geo.to_utm(17.385, 78.487)["epsg"] == 32644
    assert geo.to_utm(-33.9, 18.4)["hemisphere"] == "S" and geo.to_utm(-33.9, 18.4)["epsg"] == 32734
    assert geo.utm_zone(60, 5) == 32                               # Norway exception
    for lat, lon in [(17.385, 78.487), (-33.9, 18.4), (51.5, -0.12), (30.27, -97.74)]:
        u = geo.to_utm(lat, lon)
        la, lo = geo.from_utm(u["easting"], u["northing"], u["zone"], u["hemisphere"])
        assert abs(la - lat) < 1e-8 and abs(lo - lon) < 1e-8


def test_local_frame_accuracy():
    fr = geo.LocalFrame(17.385, 78.487)
    assert fr.to_local(17.385, 78.487) == (0.0, 0.0) or max(map(abs, fr.to_local(17.385, 78.487))) < 1e-6
    # 1 arc-second north equals the meridional radius of curvature x angle
    phi = math.radians(17.385)
    M = geo.A_WGS * (1 - geo.E2) / (1 - geo.E2 * math.sin(phi) ** 2) ** 1.5
    x, y = fr.to_local(17.385 + 1 / 3600, 78.487)
    assert abs(y - M * math.radians(1 / 3600)) < 1e-4 and abs(x) < 1e-6
    for p in [(0, 0), (125.3, -48.2), (-900, 650)]:
        la, lo = fr.to_geo(*p)
        q = fr.to_local(la, lo)
        assert math.dist(p, q) < 1e-3                               # sub-millimetre round trip
    # east axis really points east
    x, y = fr.to_local(17.385, 78.487 + 0.001)
    assert x > 100 and abs(y) < 0.01


def test_parse_location_variants():
    assert geo.parse_location("17.385, 78.4867")["lat"] == 17.385
    r = geo.parse_location("https://www.google.com/maps/place/X/@17.40,78.40,17z/data=!3m1!4b1!4m6!3m5!1s0x0:0x0!8m2!3d17.4123!4d78.4567")
    assert (r["lat"], r["lon"], r["source"]) == (17.4123, 78.4567, "google_place_pin")   # pin beats viewport
    r = geo.parse_location("https://www.google.com/maps/@17.40,78.40,17z")
    assert r["source"] == "google_viewport_centre" and "note" in r
    assert geo.parse_location("https://maps.google.com/?q=17.5,78.6")["lon"] == 78.6
    r = geo.parse_location("17°23'06.0\"N 78°29'12.1\"E")
    assert abs(r["lat"] - 17.385) < 1e-4 and abs(r["lon"] - 78.48669) < 1e-4
    r = geo.parse_location("33°55'12\"S 18°25'30\"E")
    assert r["lat"] < 0 and abs(r["lat"] + 33.92) < 1e-6 and abs(r["lon"] - 18.425) < 1e-6
    assert geo.parse_location("https://maps.app.goo.gl/abc123")["status"] == "needs_resolution"
    assert geo.parse_location("95, 10")["status"] == "error"
    assert geo.parse_location("hello")["status"] == "error"


def _square(lat, lon, half_m, fr):
    return [list(fr.to_geo(x, y)[::-1]) for x, y in [(-half_m, -half_m), (half_m, -half_m), (half_m, half_m), (-half_m, half_m), (-half_m, -half_m)]]


def test_geojson_import_export_roundtrip():
    fr = geo.LocalFrame(17.385, 78.487)
    roof = _square(17.385, 78.487, 15, fr)
    hole = [list(fr.to_geo(x, y)[::-1]) for x, y in [(-2, -2), (-2, 2), (2, 2), (2, -2), (-2, -2)]]
    tank = [list(fr.to_geo(x, y)[::-1]) for x, y in [(8, 8), (11, 8), (11, 11), (8, 11), (8, 8)]]
    tree = [list(fr.to_geo(x, y)[::-1]) for x, y in [(0, -25), (4, -25), (4, -21), (0, -21), (0, -25)]]
    fc = {"type": "FeatureCollection", "features": [
        {"type": "Feature", "properties": {"role": "roof", "name": "Main", "parapet_height_m": 0.9},
         "geometry": {"type": "Polygon", "coordinates": [roof, hole]}},
        {"type": "Feature", "properties": {"role": "obstacle", "name": "Tank", "height_m": 2.2},
         "geometry": {"type": "Polygon", "coordinates": [tank]}},
        {"type": "Feature", "properties": {"role": "tree", "name": "Neem", "height_m": 9, "transmittance": 0.3},
         "geometry": {"type": "Polygon", "coordinates": [tree]}},
        {"type": "Feature", "properties": {"role": "obstacle", "name": "Vent"},
         "geometry": {"type": "Polygon", "coordinates": [[[78.4871, 17.3851], [78.48711, 17.3851], [78.48711, 17.38511], [78.4871, 17.3851]]]}}]}
    out = geo.import_geojson(fc, origin=(17.385, 78.487))
    r = out["roofs"][0]
    assert abs(gm.area(r["polygon"]) - 900) < 0.05                    # 30 m x 30 m, survives projection
    kinds = {o["name"]: o["kind"] for o in r["obstacles"]}
    assert kinds["Main void 1"] == "keepout" and kinds["Tank"] == "obstacle" and kinds["Neem"] == "tree"
    assert any("Vent" in w for w in out["warnings"])                  # missing height flagged
    back = geo.export_geojson(out["origin"], {"roof": [{"name": "Main", "polygon": r["polygon"]}]})
    ring = back["features"][0]["geometry"]["coordinates"][0]
    assert ring[0] == ring[-1] and all(abs(a - b) < 1e-7 for p, q in zip(ring, roof) for a, b in zip(p, q))
    assert _raises(ValueError, geo.import_geojson, {"type": "FeatureCollection", "features": [
        {"type": "Feature", "properties": {"role": "roof"}, "geometry": {"type": "Polygon", "coordinates": [[[78, 17], [78.1, 17], [78, 17.1]]]}}]})
    # The swapped case is geometrically valid; correctness depends on user data. Check out-of-range instead:
    assert _raises(ValueError, geo.import_geojson, {"type": "FeatureCollection", "features": [
        {"type": "Feature", "properties": {"role": "roof"}, "geometry": {"type": "Polygon",
         "coordinates": [[[200, 17], [200.1, 17], [200.1, 17.1], [200, 17]]]}}]})


def _dxf(entities, units=6):
    head = f"0\nSECTION\n2\nHEADER\n9\n$INSUNITS\n70\n{units}\n0\nENDSEC\n" if units is not None else ""
    body = ""
    for layer, pts in entities:
        body += f"0\nLWPOLYLINE\n8\n{layer}\n90\n{len(pts)}\n70\n1\n" + "".join(f"10\n{x}\n20\n{y}\n" for x, y in pts)
    return head + "0\nSECTION\n2\nENTITIES\n" + body + "0\nENDSEC\n0\nEOF\n"


def test_dxf_import_units_layers_heights():
    txt = _dxf([("ROOF", [(0, 0), (20000, 0), (20000, 10000), (0, 10000)]),
                ("OBSTACLE_H2.5", [(5000, 4000), (7000, 4000), (7000, 6000), (5000, 6000)]),
                ("FIRE_PATH", [(10000, 0), (11000, 0), (11000, 10000), (10000, 10000)]),
                ("TANK", [(15000, 2000), (16000, 2000), (16000, 3000), (15000, 3000)])], units=4)   # millimetres
    out = geo.import_dxf(txt)
    r = out["roofs"][0]
    assert abs(gm.area(r["polygon"]) - 200) < 1e-6
    ob = {o["name"].split("-")[0]: o for o in r["obstacles"]}
    assert ob["OBSTACLE_H2.5"]["height_m"] == 2.5 and ob["FIRE_PATH"]["kind"] == "keepout"
    assert any("TANK" in w for w in out["warnings"])
    assert any("$INSUNITS" in w for w in geo.import_dxf(_dxf([("ROOF", [(0, 0), (5, 0), (5, 5)])], units=None))["warnings"])
    assert _raises(ValueError, geo.import_dxf, _dxf([("WALL", [(0, 0), (5, 0), (5, 5)])]))


def test_multi_roof_site_aggregates():
    base = legacy_to_request({"name": "Campus", "latitude": 17.385, "longitude": 78.487,
                              "roof": [{"x": 0, "y": 0}, {"x": 1, "y": 0}, {"x": 1, "y": 1}]})
    base["site"].pop("roof")
    base["site"]["roofs"] = [
        {"name": "Block A", "polygon": [[0, 0], [20, 0], [20, 12], [0, 12]], "obstacles": []},
        {"name": "Block B", "polygon": [[40, 0], [55, 0], [55, 15], [40, 15]],
         "obstacles": [{"name": "Lift", "polygon": [[46, 6], [49, 6], [49, 9], [46, 9]], "height_m": 3}]}]
    res = run_site(base)
    a, b = [r["result"] for r in res["roofs"]]
    assert res["panel_count"] == a["panel_count"] + b["panel_count"] > 0
    assert abs(res["dc_kwp"] - (a["dc_kwp"] + b["dc_kwp"])) < 1e-6
    mod = [l for l in res["boq"] if l["code"] == "PV-MOD"][0]
    assert mod["qty"] == res["panel_count"]
    assert b["shading"]["beam_weighted_shading_pct"] > a["shading"]["beam_weighted_shading_pct"]


# ---------------- equipment catalogue ----------------
GOOD_MODULE = {"pmax_w": 550, "voc_v": 49.9, "vmp_v": 41.7, "isc_a": 14.0, "imp_a": 13.19, "beta_voc_pct": -0.28,
               "gamma_pmax_pct": -0.35, "length_m": 2.278, "width_m": 1.134, "max_system_v": 1500,
               "efficiency_pct": 21.3}   # TEST FIXTURE values, not a real product record
GOOD_INV = {"ac_kw": 60, "max_dc_v": 1100, "mppt_min_v": 200, "mppt_max_v": 1000, "mppt_count": 4,
            "strings_per_mppt": 2, "max_input_current_per_mppt_a": 30, "max_isc_per_mppt_a": 40, "phases": 3,
            "grid_voltage_v": 415}


def test_spec_plausibility_checks():
    clean, warn = EQ.validate_specs("module", GOOD_MODULE)
    assert any("beta_Vmp" in w for w in warn)
    for bad in [{"vmp_v": 50.0}, {"imp_a": 15.0}, {"pmax_w": 600}, {"width_m": 0.6}, {"beta_voc_pct": 0.3}, {"foo": 1}]:
        assert _raises(ValidationFailed, EQ.validate_specs, "module", {**GOOD_MODULE, **bad}), bad
    assert _raises(ValidationFailed, EQ.validate_specs, "inverter", {**GOOD_INV, "mppt_max_v": 1200})
    assert _raises(ValidationFailed, EQ.validate_specs, "inverter", {**GOOD_INV, "phases": 2})
    assert _raises(ValidationFailed, EQ.validate_specs, "inverter", {k: v for k, v in GOOD_INV.items() if k != "ac_kw"})


def test_four_eyes_verification_workflow():
    con = _db()
    admin, _ = _company(con, "Unite Solar", "ravi@unite.example")
    designer = _member(con, admin, "d@unite.example", "solar_designer")
    eng1 = _member(con, admin, "e1@unite.example", "electrical_engineer")
    eng2 = _member(con, admin, "e2@unite.example", "electrical_engineer")
    sales = _member(con, admin, "s@unite.example", "sales_executive")
    assert _raises(Forbidden, EQ.create, con, sales, kind="module", manufacturer="X", model="Y", specs=GOOD_MODULE)
    eid = EQ.create(con, designer, kind="module", manufacturer="TestCo", model="TC-550", specs=GOOD_MODULE)["id"]
    assert _raises(Conflict, EQ.create, con, designer, kind="module", manufacturer="TestCo", model="TC-550", specs=GOOD_MODULE)
    assert _raises(ValidationFailed, EQ.submit, con, designer, eid)               # no datasheet yet
    EQ.update(con, designer, eid, specs=GOOD_MODULE, revision=1, datasheet_url="https://example.com/tc550.pdf")
    EQ.submit(con, designer, eid)
    assert _raises(Forbidden, EQ.verify, con, admin, eid, revision=2)             # admin lacks equipment.verify
    assert _raises(Conflict, EQ.verify, con, eng1, eid, revision=1)               # stale review
    EQ.verify(con, eng1, eid, revision=2, note="checked against datasheet rev B")
    spec = EQ.for_design(con, designer, eid, "module")
    assert spec["verified"] is True and spec["model"] == "TestCo TC-550"
    # editing a verified record invalidates verification, and the editor cannot self-verify
    r = EQ.update(con, eng1, eid, specs={**GOOD_MODULE, "beta_vmp_pct": -0.36}, revision=2)
    assert r["status"] == "pending_verification" and EQ.for_design(con, designer, eid, "module")["verified"] is False
    assert _raises(Forbidden, EQ.verify, con, eng1, eid, revision=3)
    EQ.verify(con, eng2, eid, revision=3)
    assert [h["action"] for h in EQ.get(con, eng2, eid)["history"]] == ["created", "updated", "submitted", "verified", "updated", "verified"]
    assert _raises(ValidationFailed, EQ.for_design, con, designer, eid, "inverter")   # wrong kind
    EQ.retire(con, admin, eid)
    assert _raises(ValidationFailed, EQ.for_design, con, designer, eid, "module")   # retired


def test_catalogue_tenant_isolation_and_csv_import():
    con = _db()
    a, _ = _company(con, "Unite Solar", "ravi@unite.example")
    b, _ = _company(con, "Other EPC", "boss@other.example")
    eid = EQ.create(con, a, kind="inverter", manufacturer="InvCo", model="I-60", specs=GOOD_INV)["id"]
    assert _raises(NotFound, EQ.get, con, b, eid)
    assert _raises(NotFound, EQ.for_design, con, b, eid, "inverter")
    assert EQ.list_equipment(con, b) == [] and len(EQ.list_equipment(con, a, kind="inverter")) == 1
    assert _raises(Forbidden, EQ.create, con, a, kind="module", manufacturer="P", model="Q", specs=GOOD_MODULE, platform=True)
    hdr = ",".join(EQ.CSV_TEMPLATE_MODULE)
    row = lambda model, **over: ",".join(str({**GOOD_MODULE, "manufacturer": "CsvCo", "model": model,
                                               "datasheet_url": "https://x/y.pdf", **over}.get(c, "")) for c in EQ.CSV_TEMPLATE_MODULE)
    res = EQ.import_csv(con, a, "module", "\n".join([hdr, row("M1"), row("M2", vmp_v=60), row("M1")]))
    assert len(res["created"]) == 1 and len(res["rejected"]) == 2
    assert all(e["status"] == "draft" for e in EQ.list_equipment(con, a, kind="module"))   # never auto-verified
