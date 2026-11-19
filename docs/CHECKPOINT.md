# Development checkpoint — 31 Oct 2026, v0.8

## v0.8 additions
Gemini provider rewritten against current docs (header auth, gemini-flash-latest default, thought-signature replay),
AI roof/obstacle detection (Stage 4), ai-check CLI, per-org AI limits. 82 Python tests + 2 browser E2E pass. ≈ 62 %.
First live step: set GEMINI_API_KEY on a connected server and run `python -m backend.cli ai-check`.

---
# Previous checkpoint (v0.7

## v0.7 additions
Map workspace, 3-D viewer + shadow animation, AI assistant (scripted-model tested), protection sizing, backup/restore,
upload validation, browser E2E test (found and fixed a designer race condition). 76 Python tests pass. Overall ≈ 59 %.
First step on a connected machine: set GEMINI_API_KEY and a licensed satellite tile URL, then run the full API suite.

---
# Previous checkpoint (v0.6

## v0.6 additions (Stages 11-18, 20)
SLD generator (SVG/DXF/PDF, consistency-checked), structural quantities & loads, rule-based BOQ completion, EMI/BOOT/RESCO/
group-captive/metering models, BESS dispatch, CRM, procurement, EPC execution, Operations screen, proposal/RFQ/commissioning
reports. `python tests/run_tests.py` → 66 passed (procurement and EPC gates mutation-tested). Overall ≈ 54 % (see tracker).

---
# Previous checkpoint (v0.5 (engine 0.3.0 + identity + equipment catalogue + geo backend)

## v0.5 additions
Stage 9 equipment catalogue (four-eyes verification) and Stage 3 geo backend (coordinates, UTM, Google Maps links,
GeoJSON/DXF import, multi-roof). `python tests/run_tests.py` → 54 passed. Catalogue + geo HTTP routes smoke-tested with
stand-ins (cross-tenant equipment → 404, self-verification → 403, anonymous catalogue design → 401).

---
# Previous checkpoint (v0.4)

## Verified in session (no internet; FastAPI/pydantic not installable)
`python tests/run_tests.py` → **45 passed, 0 failed** (28 engine benchmarks, 17 identity/tenancy/RBAC).
Tenant isolation mutation-tested: weakening the org check makes 2 tests fail.
HTTP routes: syntax-checked and exercised end-to-end against stand-in FastAPI modules (register → verify → login →
save → conflict → update → revisions → invite → audit → logout → 401 → rate-limit 429). One bug found and fixed
that way (rate limiter returned 500 instead of 429). Real FastAPI tests have **not** run.

## Resume order (needs internet)
1. `pip install -r requirements.txt && pytest -q` — run the 11 API tests; fix what they expose.
2. pvlib / PySAM cross-validation of solar position and energy → `docs/VALIDATION.md`.
3. PVGIS live check for Hyderabad (PR 0.75–0.85 expected).
4. Port migrations to PostgreSQL (+PostGIS) with row-level security mirroring `_access()`; keep SQLite for tests.
5. Stage 3 front-end: MapLibre workspace on top of the finished geo backend; choose a licensed imagery provider.
5b. Load Unite Solar's real module/inverter datasheets via the CSV template; engineers verify them.
6. Next.js front-end against `/api/*` (auth flows already defined).

## First-run on a server
```
cp .env.example .env            # edit values
python -m backend.cli migrate
python -m backend.cli create-admin --email you@unitesolar.example --name "Your Name"
uvicorn backend.app:app
python -m backend.cli outbox    # dev: read verification links until SMTP is configured
```
