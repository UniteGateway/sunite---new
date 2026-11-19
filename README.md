# SUNITE AI Solar Designer — engineering preview v0.9

## v0.9 — DPR, layout editing API, path to release
Consolidated Detailed Project Report, manual-layout validation (`/api/v3/layout/validate`) and scenario comparison
(`/api/v3/scenarios/compare`). Remaining work to release: `docs/PATH_TO_100.md`. Tests: `python tests/run_tests.py` (85).


## v0.8 — Gemini connected
Production Gemini provider (current REST API, key in header, retries, usage limits) for the AI assistant and
**AI roof/obstacle detection** on the map. Setup: `docs/GEMINI_SETUP.md`, then `python -m backend.cli ai-check`.
Tests: `python tests/run_tests.py` (82).


## v0.7 — map, 3-D, AI assistant
`/map` trace roofs on a tile map and send them to the designer · `/viewer` 3-D view with sun-path shadow animation ·
`/assistant` AI engineering assistant (set GEMINI_API_KEY) that answers only from engine results and needs your approval
for any design change · protection/earthing/transformer sizing · `python -m backend.cli backup|restore`.
Tests: `python tests/run_tests.py` (76), `node tests/viewer3d.test.js`, `node tests/mapws.test.js`,
browser E2E: `python tests/e2e/preview_server.py &` then `python tests/e2e/test_map_to_design.py`.


## v0.6 — engineering-to-execution chain
Single-line diagrams, structural quantities, complete traceable BOQ, commercial models (CAPEX, EMI, BOOT/RESCO, group
captive), battery dispatch, CRM pipeline, procurement (RFQ → comparison → approved PO → goods receipt), EPC execution
(WBS, commissioning per string, handover) and customer proposals. Screens: `/`, `/equipment`, `/operations`.
Status by stage: `docs/IMPLEMENTATION_TRACKER.md`. Tests: `python tests/run_tests.py` (66).


## v0.5 — equipment library and geo-referencing
`/equipment`: module & inverter catalogue with datasheet verification by a second engineer. Designs can reference
catalogue ids (`module_id`, `inverter_id` on `/api/v3/design`). Paste a Google Maps link to set the site location.
GeoJSON and DXF roof imports, multiple roofs per project (`site.roofs`). Tests: `python tests/run_tests.py` (54).


## v0.4 — accounts, organisations, roles and tenant isolation
Sign-in is now required to save projects (set `SUNITE_AUTH_MODE=local_demo` for single-user local use).
Each organisation sees only its own projects unless a project is explicitly shared. Nine roles with an enforced
permission matrix; full audit log; revision history. See `docs/SECURITY_MODEL.md` and `docs/CHECKPOINT.md`.
Existing v0.2 saved projects are preserved and imported with `POST /api/org/import-legacy-projects`.

All dependency-free tests: `python tests/run_tests.py` (45 tests).


## v0.3 (31 Oct 2026) — new engineering engine
`backend/engine/` is a framework-free, tested core: layout on irregular roofs, 3-D ray-cast shading (obstacles,
parapets, trees, row-to-row; beam-weighted), DC string design with full temperature/current checks, voltage-drop cable
sizing, hourly energy simulation from real weather (PVGIS or CSV — **no weather, no energy figures**), traceable BOQ,
finance maths, branded PDF report and Excel BOQ. See `docs/` for the audit, tracker, checkpoint and v3 API.

Run engine tests without any extra packages: `python tests/run_engine_tests.py`  (28 tests)
Run everything: `pip install -r requirements.txt && pytest -q`

---
# Original v0.2 notes (superseded where v0.3 says otherwise)

An original proprietary app **built with** open-source packages, not copied third-party repository code. MIT/BSD open-source geometry/CAD libraries provide the core reusable capabilities. The software is NOT a certified engineering or automatic satellite-shading solution.

## Run

```bash
python -m venv .venv
source .venv/bin/activate  # Windows: .venv\\Scripts\\activate
pip install -r requirements.txt
uvicorn backend.app:app --reload
```
Open http://localhost:8000/ and click **Generate design**. Use demo roof or Draw roof / Add obstacle / Close polygon. Export Excel BOQ and DXF roof drawing. Save/reload project JSON.

## Test

```bash
pytest -q
```

## Open-source components and upstream repositories

- [Shapely](https://github.com/shapely/shapely) — BSD-3-Clause — polygon geometry and automated module placement.
- [pvlib-python](https://github.com/pvlib/pvlib-python) — BSD-3-Clause — astronomical solar positioning when installed; approximate fallback if unavailable.
- [ezdxf](https://github.com/mozman/ezdxf) — MIT — downloadable CAD roof/module DXF.
- [openpyxl](https://github.com/theorchard/openpyxl) — MIT — preliminary Excel BOQ.
- [FastAPI](https://github.com/fastapi/fastapi) — MIT — backend API.
- [MapLibre GL JS](https://github.com/maplibre/maplibre-gl-js) — BSD-3-Clause — **planned** live map integration; not used in this version.
- [SimShady](https://github.com/open-pv/simshady) — Apache-2.0 — **candidate** for future validated 3D shadow integration, not integrated or tested yet.
- [NREL PySAM](https://github.com/NREL/pysam) — **planned**, not integrated or tested yet.

Read each installed dependency's current LICENSE, notices and redistribution rules before commercial release. No vendor imagery and no paid APIs are required for this prototype.

## Current functional scope

- Single-project roof polygons in local metres
- Obstacle exclusion polygons and height inputs
- Rotatable module grid with clearance and row spacing
- Multiple seasonal solar-position/obstacle shadow **samples** (centre-point exposure; not energy-weighted annual loss)
- Indicative DC power, baseline annual yield, savings, installed cost, module quantities
- Excel preliminary BOQ and CAD DXF export
- Project JSON import/export
- Basic REST API + tests

## Critical limitations

1. **No georeferenced satellite building detection or map overlay.** Entered lat/lon only informs sun position; roof shape is manually drawn in local metre coordinates.
2. **No bankable 3D shading simulation.** Shading samples use simple extruded obstacle envelopes. The orange 'shadow overlay' button currently only explains the missing overlay; it does not render shadows.
3. **No true cable lengths, rail steel tonnage, module string voltage, equipment selection, wind loading, structure anchoring, electrical protections or code compliance.** The BOQ explicitly labels unknown quantities TBD.
4. **Yield and budget are user assumptions**, not computed irradiance/financial forecasts. Shading sample percentages must not be interpreted as generation losses.
5. **No authentication, persistent server-side database, CRM, billing, multi-tenant security or deployment infrastructure.** These are required before live commercial operation.
6. Drawings must be measured and approved by qualified structural/electrical engineers; local laws and OEM datasheets must be verified.

## Next engineering milestones

PostGIS georeferenced roof editor, imagery rights/provider, true 3D scene heights, raytraced shade-time series, POA irradiance and inverter models, validated solar yield, manufacturer catalogs, DC string constraint solver, cable-route metering, structure structural design integration, versioned BOQ and project approvals, authentication, database and CI/CD.

## v0.2 additions (31 Oct 2026)
- SQLite project persistence: list/create/read/update/delete; updates require `If-Match` revision integer and reject stale edits with HTTP 409.
- OpenStreetMap reference map from latitude/longitude (needs internet when browser loads map; **not** auto roof identification or commercial satellite tiles).
- Editable module Voc/Vmp and inverter voltage assumptions in web UI; approximate cold Voc and hot Vmp screening, with explicit failure flag for invalid strings. This is **not** an approved electrical design.
- Provisional conductor route estimate if inverter and AC connection points are supplied in local roof coordinates (via JSON/API), using straight-line distances and user route factors; must not be used as a purchase quantity without site measurements.
- Optional BOQ unit rates, line amounts and partial priced total in API/Excel; missing rates stay blank and are not treated as zero-cost equipment.
- Shading-sample CSV download and 7 API tests.

## Additional API endpoints
- `GET /api/projects` List saved projects (local-only demo storage).
- `POST /api/projects` Validate and save new site.
- `GET /api/projects/{id}` Load saved project.
- `PUT /api/projects/{id}` Update with required `If-Match: <revision>` header.
- `DELETE /api/projects/{id}` Delete saved project.
- `POST /api/export/shading.csv` Export sampled sunlight/shadow snapshots.

### Deployment warning
**DO NOT deploy this demo to a public internet server.** It has no authentication or per-tenant permissions, and uses a local single-operator SQLite file. Implement OAuth/SSO, authorization, rate limits, input quotas, storage isolation, CSRF protection if using cookies, audit logs, encrypted backups, TLS, logging and production monitoring before a real launch. A local project file is not a replacement for a production database. External basemaps cannot accurately provide obstacle heights; verified elevation data/survey is required for high-fidelity solar shading.

### Not yet implemented
- Automatic detection of roof outlines from aerial or satellite imagery, verified elevation/photogrammetry/LiDAR ingestion, full raytraced 3D seasonal shading, irradiation-weighted yield loss, PVsyst-grade simulation, manufacturer module/inverter catalog and MPPT current validation, professional structural analysis, complete cable schedule, protection coordination, electrical SLD, full procurement-grade BOQ, multi-tenant login/permissions, billing, vendor integrations, production CI/CD and cloud hosting.

The original and updated outputs remain **preliminary planning aids** only; a qualified electrical/structural professional must validate designs and site conditions.
