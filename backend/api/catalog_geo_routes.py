"""HTTP routes: equipment catalogue (Stage 9) and geo-referencing / imports (Stage 3)."""
from __future__ import annotations
from fastapi import Header, HTTPException, Request
from fastapi.responses import PlainTextResponse
from pydantic import BaseModel, Field
from backend.engine import geo
from backend.services import equipment as EQ
from backend.api.identity_routes import db, principal, run


class EquipmentIn(BaseModel):
    kind: str
    manufacturer: str = Field(max_length=80)
    model: str = Field(max_length=80)
    specs: dict
    datasheet_url: str | None = Field(default=None, max_length=500)
    datasheet_sha256: str | None = Field(default=None, max_length=64)
    datasheet_revision: str | None = Field(default=None, max_length=40)
    platform: bool = False

class EquipmentUpdate(BaseModel):
    specs: dict
    revision: int
    datasheet_url: str | None = Field(default=None, max_length=500)
    datasheet_sha256: str | None = Field(default=None, max_length=64)
    datasheet_revision: str | None = Field(default=None, max_length=40)
    note: str | None = Field(default=None, max_length=500)

class VerifyIn(BaseModel):
    revision: int
    note: str = Field(default="", max_length=500)

class CsvIn(BaseModel):
    kind: str
    csv: str = Field(max_length=1_000_000)

class LocationIn(BaseModel):
    text: str = Field(max_length=2000)

class GeoJsonIn(BaseModel):
    geojson: dict
    origin_lat: float | None = None
    origin_lon: float | None = None

class DxfIn(BaseModel):
    dxf: str = Field(max_length=20_000_000)
    units_scale_to_m: float | None = None

class ExportIn(BaseModel):
    origin_lat: float
    origin_lon: float
    layers: dict


def register(app):
    @app.get("/api/equipment")
    def _list(request: Request, kind: str | None = None, status: str | None = None, q: str | None = None,
              authorization: str | None = Header(default=None)):
        with db() as con:
            return run(lambda: EQ.list_equipment(con, principal(con, authorization, request), kind, status, q))

    @app.get("/api/equipment/template.csv")
    def _template(kind: str = "module"):
        cols = EQ.CSV_TEMPLATE_MODULE if kind == "module" else EQ.CSV_TEMPLATE_INVERTER
        return PlainTextResponse(",".join(cols) + "\n", media_type="text/csv",
                                 headers={"Content-Disposition": f'attachment; filename="sunite-{kind}-template.csv"'})

    @app.get("/api/equipment/{eid}")
    def _get(eid: str, request: Request, authorization: str | None = Header(default=None)):
        with db() as con:
            return run(lambda: EQ.get(con, principal(con, authorization, request), eid))

    @app.post("/api/equipment", status_code=201)
    def _create(body: EquipmentIn, request: Request, authorization: str | None = Header(default=None)):
        with db() as con:
            return run(lambda: EQ.create(con, principal(con, authorization, request), **body.model_dump()))

    @app.put("/api/equipment/{eid}")
    def _update(eid: str, body: EquipmentUpdate, request: Request, authorization: str | None = Header(default=None)):
        with db() as con:
            return run(lambda: EQ.update(con, principal(con, authorization, request), eid, **body.model_dump()))

    @app.post("/api/equipment/{eid}/submit")
    def _submit(eid: str, request: Request, authorization: str | None = Header(default=None)):
        with db() as con:
            run(lambda: EQ.submit(con, principal(con, authorization, request), eid)); return {"status": "pending_verification"}

    @app.post("/api/equipment/{eid}/verify")
    def _verify(eid: str, body: VerifyIn, request: Request, authorization: str | None = Header(default=None)):
        with db() as con:
            run(lambda: EQ.verify(con, principal(con, authorization, request), eid, revision=body.revision, note=body.note))
            return {"status": "verified"}

    @app.post("/api/equipment/{eid}/retire")
    def _retire(eid: str, request: Request, authorization: str | None = Header(default=None)):
        with db() as con:
            run(lambda: EQ.retire(con, principal(con, authorization, request), eid)); return {"status": "retired"}

    @app.post("/api/equipment/import")
    def _import(body: CsvIn, request: Request, authorization: str | None = Header(default=None)):
        with db() as con:
            return run(lambda: EQ.import_csv(con, principal(con, authorization, request), body.kind, body.csv))

    # ---------------- geo (stateless; no tenant data touched) ----------------
    def _v(fn):
        try:
            return fn()
        except ValueError as e:
            raise HTTPException(422, str(e))

    @app.post("/api/v3/geo/parse-location")
    def _loc(body: LocationIn):
        r = geo.parse_location(body.text)
        if r["status"] == "ok":
            r["utm"] = geo.to_utm(r["lat"], r["lon"])
        return r

    @app.post("/api/v3/geo/import-geojson")
    def _gj(body: GeoJsonIn):
        origin = (body.origin_lat, body.origin_lon) if body.origin_lat is not None and body.origin_lon is not None else None
        return _v(lambda: geo.import_geojson(body.geojson, origin))

    @app.post("/api/v3/geo/import-dxf")
    def _dxf(body: DxfIn):
        from backend.ops import validate_text_upload
        _v(lambda: validate_text_upload(body.dxf, kind="dxf", max_bytes=20_000_000))
        return _v(lambda: geo.import_dxf(body.dxf, body.units_scale_to_m))

    @app.post("/api/v3/geo/export-geojson")
    def _export(body: ExportIn):
        return _v(lambda: geo.export_geojson({"lat": body.origin_lat, "lon": body.origin_lon}, body.layers))
