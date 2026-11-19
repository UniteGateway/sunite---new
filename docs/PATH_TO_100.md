# Path from ~63 % to 100 % (release-ready)

Status after v0.9: 85 Python tests, JS unit tests and 2 browser E2E suites pass in the authoring sandbox (no internet).
Everything below needs internet, credentials, hosting, licences or qualified people, so it was not done here.
Effort figures are rough estimates for one experienced full-stack developer unless stated.

## A. Unite Solar decisions & inputs (owner: Unite Solar; blocking)
| # | Item | Why | Unlocks |
|---|---|---|---|
| A1 | Hosting choice (Render / Railway / AWS Mumbai) + domain | where the app runs | C1-C5 |
| A2 | Gemini key with billing (AI Studio) | live AI | B3, B4 |
| A3 | Licensed satellite imagery provider (terms must allow automated analysis) | map + AI detection | B4 |
| A4 | Verified datasheets for modules/inverters you buy (CSV template in Equipment Library) | real engineering data | B6 |
| A5 | Your engineers' cable ampacity table, voltage-drop limits, protection practice, structural section catalogue, wind factors | jurisdiction rules | B7 |
| A6 | Rate card, tax rates, tariff & subsidy figures with source dates | quotations | B8 |
| A7 | SMTP / email provider | verification & reset emails | C3 |

## B. Validation & completion with internet (owner: developer)
| # | Stage | Task | Done when | Est. |
|---|---|---|---|---|
| B1 | 1, 23 | `pip install -r requirements.txt && pytest -q`; fix anything the 26 API tests expose | all API tests green in CI | 2-3 d |
| B2 | 8 | Cross-validate solar position (pvlib SPA, < 0.02°) and energy (pvlib ModelChain / PySAM PVWatts) for 3 Indian sites; add Perez transposition; P50/P90 | deviations documented in `docs/VALIDATION.md` within agreed tolerance | 1-2 wk |
| B3 | 19 | `python -m backend.cli ai-check`; run 30 scripted engineering questions; tune prompts; measure grounding-flag rate | ≥ 90 % grounded answers, no unapproved changes | 1 wk |
| B4 | 3, 4 | Plug imagery URL; test AI detection on 20 real roofs vs. manual tracing; vertex drag-editing on map | median outline error recorded; acceptance workflow used by designers | 2 wk |
| B5 | 7 | Compare shading with SimShady/another tool on 5 sites; add diffuse (sky-view) shading | differences explained and documented | 1-2 wk |
| B6 | 9 | Load A4 data; engineers verify (four-eyes) | designs use only verified equipment | Unite + 2 d |
| B7 | 10, 12 | Encode A5 as configurable rulesets (ampacity tables, protection coordination, member checks) | electrical & structural checks reference ruleset version | 2-3 wk |
| B8 | 15 | Tariff/subsidy library with source dates per state (AP, Telangana first) | quotes reference dated sources | 1 wk |

## C. Production platform (owner: developer; needs A1)
| # | Stage | Task | Est. |
|---|---|---|---|
| C1 | 1 | Port migrations to PostgreSQL + PostGIS; row-level security mirroring `_access()`; keep SQLite for tests | 1-2 wk |
| C2 | 1, 22 | Redis rate limiting & background jobs (long shading/energy runs, cancellation) | 1 wk |
| C3 | 22 | Object storage for photos/drawings; upload malware scan; dependency audit (`pip-audit`) in CI; security headers without `unsafe-inline` | 1 wk |
| C4 | 24 | Staging + production deploy, TLS, nightly `backend.cli backup` to object storage, restore drill, monitoring/alerts, smoke tests | 1-2 wk |
| C5 | 22 | External penetration test; fix findings | 1-2 wk + vendor |

## D. Product completion (owner: developer + designer)
| # | Stage | Task | Est. |
|---|---|---|---|
| D1 | 21 | Next.js + TypeScript UI over the existing APIs (dashboard, projects, all modules), light/dark, responsive, accessibility audit | 6-8 wk |
| D2 | 5, 6 | Interactive layout editor (drag/add/remove/lock, undo/redo) using `/api/v3/layout/validate`; sloped & multi-level roofs in 3-D; glTF export | 3-4 wk |
| D3 | 16 | Hybrid inverter catalogue, BESS safety checks (spacing, ventilation, fire code inputs), BESS financial model | 2 wk |
| D4 | 17, 18 | Survey scheduling, client portal, FranchiseOS link, site-photo uploads, contractor management | 3 wk |
| D5 | 20 | BOOT/PPA-specific proposals, site survey & execution reports, branded templates per company | 1-2 wk |

## E. Engineering sign-off (owner: Unite Solar qualified engineers)
* Review benchmark results (B2, B5) and rulesets (B7); sign the validation report.
* Keep the "PRELIMINARY / NOT FOR CONSTRUCTION" marking until this sign-off exists.

## Release checklist (100 % = all ticked)
- [ ] All automated tests green in CI on PostgreSQL
- [ ] Validation report signed (energy, shading, electrical, structural)
- [ ] Penetration test passed, no high/critical findings open
- [ ] Backups + tested restore in production
- [ ] Live Gemini + imagery verified on real projects
- [ ] Real equipment catalogue verified; rate cards loaded
- [ ] Next.js UI accepted by sales, design and EPC users
- [ ] Monitoring, alerting and support process in place

Rough total: about 4-6 months for one developer, or about 2-3 months for a small team of 3, plus Unite Solar's inputs.
