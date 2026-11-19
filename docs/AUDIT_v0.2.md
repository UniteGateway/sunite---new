# Audit of the supplied v0.2 codebase (31 Oct 2026)

## What was actually supplied
5 source files: `backend/app.py` (one 260-line FastAPI module), `frontend/index.html` (single-file vanilla JS, canvas editor),
`tests/test_api.py` (7 tests), `requirements.txt`, `README.md`. No Next.js, no PostgreSQL, no migrations, no auth, no Docker, no CI.

## Claimed features vs. code
| Claimed | Verdict | Evidence |
|---|---|---|
| Rooftop polygon editor | **Works (basic)** | Canvas click-to-draw in local metres; no georeferencing |
| Automatic module placement | **Works, with defects** | Shapely grid; single grid phase (not optimised); portrait only; tilt ignored |
| Obstacle clearance | Works | `buffer(walkway_m)` — but the "walkway" field is really an obstacle clearance |
| OpenStreetMap reference | Works (iframe) | Visual only; not linked to roof geometry |
| Preliminary shading | **Defective** | See E1-E4 |
| Generation estimate | **Not a simulation** | `dc_kw × user-entered yield`; tilt, azimuth, weather and shading ignored |
| String voltage checks | Partial | Cold Voc OK; hot Vmp uses a hard-coded -0.35 %/°C; no MPPT-max, current or Isc checks |
| Cable estimates | Weak | Straight-line from every Nth module; no voltage drop/ampacity |
| Priced BOQ / Excel / DXF / CSV | Works | Items keyed by free-text names; most electrical lines `TBD` |
| Project DB + revisions | Works | SQLite with optimistic `If-Match` — but connection leak (S2) |
| 7 passing tests | **Unverifiable here; plausibly true** | Tests only check status codes / non-empty output, not engineering correctness |

## Engineering defects (fixed in v0.3 engine)
- **E1** Shading sampled at fixed **UTC 03:00-10:00** — only valid near Indian longitudes; wrong anywhere else.
- **E2** Shade test used only each module's **centre point**, at roof level (mount height ignored), parapets ignored, no row-to-row shading.
- **E3** Shade % was an **unweighted** count of snapshots; not irradiance-weighted, not annual.
- **E4** pvlib branch passed a bare `datetime` to `get_solarposition` (expects a DatetimeIndex) and only caught `ImportError`.
- **E5** `indicative_ac_kw` hard-coded `dc/1.2`, ignoring the user's `dc_ac_ratio` used elsewhere (inconsistent outputs).
- **E6** String design capped at 18 modules arbitrarily and left one short remainder string that typically failed the MPPT check.
- **E7** No north reference: roof rotation and true north were conflated.
- **E8** Row gap fixed regardless of tilt — tilted rows would self-shade.

## Security / reliability
- **S1** No authentication, authorisation or tenant isolation (README correctly warns: do not deploy publicly).
- **S2** `with sqlite3.connect()` commits but never closes — connection/file-handle leak under load.
- **S3** `innerHTML` with server data in BOQ/shading tables (low risk, fixed with escaping).
- **S4** No input-size limits on polygons/obstacles (DoS via huge payloads) — capped in v0.3.
- **S5** Static mount + `index()` re-reads file per request (minor).

## Technical debt
Single-module backend, no layering, no migrations, no structured logging, magic numbers inline, tests assert shape not correctness.
