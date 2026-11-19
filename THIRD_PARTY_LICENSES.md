# Third-party components (verify current LICENSE files before release)
| Package | Licence | Use |
|---|---|---|
| FastAPI, Starlette, Uvicorn, Pydantic, httpx | MIT / BSD-3 | API |
| NumPy | BSD-3 | shading ray-casting |
| openpyxl | MIT | Excel BOQ |
| ReportLab (open-source edition) | BSD | PDF reports |
| Matplotlib | Matplotlib licence (PSF-style, permissive) | report drawings |
| ezdxf | MIT | DXF export |
| Shapely | BSD-3 | legacy v0.2 helpers only |
| pvlib-python | BSD-3 | optional cross-validation |
| PVGIS (EU JRC) web service | Free service; cite JRC, check terms | TMY weather |
Evaluated, not integrated yet: MapLibre GL JS (BSD-3), Three.js (MIT), SimShady (check current licence), NREL PySAM (BSD-3), PyPSA (MIT).
Algorithms implemented in-house from published methods: NOAA solar position, Möller–Trumbore ray-triangle, Kasten–Young air mass, Meinel clear-sky DNI, Liu–Jordan isotropic transposition.
