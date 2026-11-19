# SUNITE AI Solar Designer — implementation tracker (v0.9, 31 Oct 2026)

Scoring: each of the 25 items (audit + 24 stages) counts equally; the % is my estimate of how much of that stage's
specification is implemented **and tested in this sandbox**. Code that was written but not run is not counted as done.

| # | Stage | % | What exists (tested) | Main gaps |
|---|---|---|---|---|
| 0 | Audit | 100 | `docs/AUDIT_v0.2.md` | — |
| 1 | Foundation | 60 | config guards, JSON logging, checksum migrations, CLI, security headers | PostgreSQL/PostGIS, Redis; CI & Docker written but not run |
| 2 | Auth & organisations | 80 | sign-up/verify/login/lockout/sessions/reset/invites, 9 roles, tenant isolation (mutation-tested) | MFA, SSO, Postgres RLS |
| 3 | GIS | 65 | geo backend + map workspace: tile map (configurable provider), trace roofs/obstacles/keep-outs/trees, live area, GeoJSON export, send-to-designer (browser E2E-tested) | live imagery not tested (offline), vertex drag-editing, drone/survey import |
| 4 | AI roof detection | 35 | Gemini vision: view composed from licensed tiles or upload, structured JSON polygons, georeferencing (tested to 1e-7°), validation, confidence, unverified suggestions, accept-with-measured-height UI (browser-tested with canned output) | never run on a real model/imagery; no drone/LiDAR heights |
| 5 | 2D layout | 72 | auto placement on irregular roofs + multi-roof; manual-edit validation API (setback/clearance/overlap/locked), scenario comparison | drag-and-drop editor UI, undo/redo |
| 6 | 3D model | 50 | dependency-free 3-D viewer: roof, parapets, obstacles, tilted modules, orbit/zoom, top/south/east/perspective, shading colours (view conventions unit-tested) | multi-elevation & sloped roofs, model export |
| 7 | Shadow analysis | 72 | + sun-path per day, instant shadow rendering, day animation, solstice/equinox presets | diffuse shading, SimShady comparison |
| 8 | Energy | 60 | hourly chain, CSV ingest, PVGIS client (untested) | pvlib/PySAM validation, Perez, P90 |
| 9 | Equipment DB | 80 | catalogue, plausibility checks, four-eyes verification, CSV import, Library page | real Unite Solar datasheets; PDF extraction |
| 10 | Electrical | 75 | + string fuse rule, AC breakers, main breaker, earthing conductor (adiabatic), transformer selection | jurisdiction ampacity tables, protection coordination |
| 11 | SLD | 70 | generated from design, SVG/DXF/PDF, tagged schedule, consistency check | hybrid/ground-plant variants, editing |
| 12 | Structural | 55 | frames, members, steel tonnage, anchors/ballast, wind uplift from input factors, roof load flags | member checks, carports/sheds variants, code tables |
| 13 | BOQ | 70 | traceable lines from layout/electrical/SLD/structure/rules, pricing, tax, freight, contingency | full material catalogue; auto-update on approval |
| 14 | Procurement | 75 | vendors, RFQ from approved revision, quotes, comparison, PO with 2-person approval, GRN, inventory, price history, RFQ PDF, comparison xlsx | supplier performance, warehouse transfers UI |
| 15 | Commercials | 70 | CAPEX, EMI/loan, BOOT/RESCO with DSCR & tax, group captive, net/gross/zero-export | tariff/subsidy library with source dates; UI |
| 16 | BESS | 55 | hourly dispatch, self-consumption/peak-shaving/backup reserve, export limits, balance checks, size sweep | hybrid inverter selection, safety checks, BESS finance |
| 17 | CRM | 65 | customers (dedupe), leads, stage rules, activities, quotations with approval, pipeline board | survey scheduling entity, client portal UI, FranchiseOS link |
| 18 | EPC | 65 | WBS from design, dependencies, logs, punch list, per-string commissioning checklist, sign-off gates, handover, warranties, O&M | photo storage, contractor management |
| 19 | AI assistant | 55 | + production Gemini provider: key in header (mutation-tested), retries/backoff, thought-signature & call-id replay, safe errors, model listing, usage logging, per-org daily cap, `ai-check` CLI; all HTTP-level tested with a fake transport | not yet run against the live API (needs your key + internet) |
| 20 | Reports | 68 | + consolidated DPR (summary, layout, shading, energy, electrical+SLD, structure, BOQ, financials, schedule, assumptions) | BOOT/PPA-specific proposals, site-survey & execution reports |
| 21 | UI/UX | 40 | designer, map, 3-D viewer, AI assistant, equipment library, operations | Next.js rebuild, dashboards, dark mode |
| 22 | Security | 58 | + verified backup/restore with checksum & integrity check, upload validation | dependency scan run, upload malware scan, pen-test |
| 23 | Testing | 60 | 82 Python tests + JS unit tests + 2 browser E2E suites; mutation checks | real FastAPI API tests (26 written) not run; pvlib validation |
| 24 | Deployment | 10 | Dockerfile, CI file, env template | staging, TLS, backups, monitoring, smoke tests |

**Overall: 15.65 / 25 ≈ 63 %.** The rest is listed task-by-task with owners and estimates in `docs/PATH_TO_100.md`.