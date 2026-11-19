"""Hourly energy simulation from a user-supplied or fetched weather series.

No weather data -> no generation numbers (status 'weather_required'). The engine
never fabricates irradiance. Model chain:
  sun position -> isotropic-sky transposition (Liu-Jordan) -> beam shading factor
  (from the shading engine, applied to beam ONLY, so shading is not double-counted)
  -> NOCT cell temperature -> PVWatts-style DC with gamma_Pmax -> DC loss chain
  -> inverter efficiency + AC clipping -> AC energy.
"""
from __future__ import annotations
import csv, io, math
from datetime import datetime, timezone
from .solar import sun_position

DEFAULT_LOSSES_PCT = {  # project assumptions; every value visible & editable
    "soiling": 2.0, "module_mismatch": 1.0, "dc_wiring": 1.5, "lid": 1.5,
    "ac_wiring": 0.5, "availability": 0.5,
}


def poa_isotropic(ghi, dni, dhi, zenith, sun_az, tilt, surf_az, albedo=0.2):
    t, z = math.radians(tilt), math.radians(zenith)
    cos_aoi = (math.cos(z) * math.cos(t) +
               math.sin(z) * math.sin(t) * math.cos(math.radians(sun_az - surf_az)))
    beam = max(0.0, dni * cos_aoi) if zenith < 90 else 0.0
    sky = dhi * (1 + math.cos(t)) / 2
    ground = ghi * albedo * (1 - math.cos(t)) / 2
    return beam, sky, ground, cos_aoi


def parse_weather_csv(text: str) -> list[dict]:
    """Columns (case-insensitive): time_utc (ISO-8601), ghi, dni, dhi, temp_air [, wind]."""
    rdr = csv.DictReader(io.StringIO(text))
    norm = {k: k.strip().lower() for k in (rdr.fieldnames or [])}
    need = {"time_utc", "ghi", "dni", "dhi", "temp_air"}
    if not need <= set(norm.values()):
        raise ValueError(f"weather CSV must contain columns {sorted(need)}")
    rows = []
    for raw in rdr:
        r = {norm[k]: v for k, v in raw.items()}
        t = datetime.fromisoformat(r["time_utc"].replace("Z", "+00:00"))
        if t.tzinfo is None:
            t = t.replace(tzinfo=timezone.utc)
        vals = {k: float(r[k]) for k in ("ghi", "dni", "dhi", "temp_air")}
        if min(vals["ghi"], vals["dni"], vals["dhi"]) < 0:
            raise ValueError(f"negative irradiance at {r['time_utc']}")
        rows.append({"time": t, **vals})
    if len(rows) < 8000:
        raise ValueError("weather series must be a full year of hourly data (>= 8000 rows)")
    return rows


def simulate(weather: list[dict] | None, *, latitude, longitude, tilt, azimuth, dc_kwp, ac_kw,
             gamma_pmax_pct, noct_c=45.0, albedo=0.2, beam_shading_pct=0.0,
             losses_pct: dict | None = None, inverter_eff_pct=98.0, degradation_pct_per_year=None,
             years=25, weather_source="user file") -> dict:
    if not weather:
        return {"status": "weather_required",
                "message": "Upload a TMY/hourly weather CSV or fetch PVGIS TMY. No generation figures are produced without irradiance data."}
    losses = {**DEFAULT_LOSSES_PCT, **(losses_pct or {})}
    dc_factor = 1.0
    for v in losses.values():
        dc_factor *= 1 - v / 100
    sh = 1 - beam_shading_pct / 100
    monthly = [0.0] * 12
    poa_month = [0.0] * 12
    ac_total = poa_total = clipped = 0.0
    hourly = []
    for w in weather:
        sp = sun_position(w["time"], latitude, longitude)
        if sp.zenith >= 90 or w["ghi"] <= 0:
            hourly.append(0.0)
            continue
        b, s, gnd, _ = poa_isotropic(w["ghi"], w["dni"], w["dhi"], sp.zenith, sp.azimuth, tilt, azimuth, albedo)
        poa = b * sh + s + gnd
        tc = w["temp_air"] + (noct_c - 20) / 800 * poa
        pdc = dc_kwp * poa / 1000 * (1 + gamma_pmax_pct / 100 * (tc - 25)) * dc_factor
        pac = max(0.0, pdc * inverter_eff_pct / 100)
        if pac > ac_kw:
            clipped += pac - ac_kw
            pac = ac_kw
        hourly.append(pac)
        m = w["time"].month - 1
        monthly[m] += pac
        poa_month[m] += (b + s + gnd) / 1000
        ac_total += pac
        poa_total += (b + s + gnd) / 1000
    pr = ac_total / (dc_kwp * poa_total) if poa_total and dc_kwp else None
    out = {
        "status": "ok", "weather_source": weather_source, "hours": len(weather),
        "annual_ac_kwh": round(ac_total, 1),
        "monthly_ac_kwh": [round(x, 1) for x in monthly],
        "annual_poa_kwh_m2": round(poa_total, 1),
        "specific_yield_kwh_per_kwp": round(ac_total / dc_kwp, 1) if dc_kwp else None,
        "performance_ratio": round(pr, 4) if pr else None,
        "cuf_ac_pct": round(100 * ac_total / (ac_kw * len(weather)), 2) if ac_kw else None,
        "clipping_kwh": round(clipped, 1),
        "assumptions": {"transposition": "isotropic (Liu-Jordan)", "albedo": albedo, "noct_c": noct_c,
                        "gamma_pmax_pct": gamma_pmax_pct, "inverter_eff_pct": inverter_eff_pct,
                        "beam_shading_pct_applied": beam_shading_pct, "dc_losses_pct": losses},
        "uncertainty_note": "Typical TMY inter-annual variability is several percent; isotropic transposition "
                            "tends to under-estimate POA on tilted planes versus Perez. Treat as P50-style estimate only.",
    }
    out["hourly_ac_kw"] = [round(x, 4) for x in hourly]
    if degradation_pct_per_year is not None:
        out["lifetime_kwh"] = [round(ac_total * (1 - degradation_pct_per_year / 100) ** y, 1) for y in range(years)]
    return out


def fetch_pvgis_tmy(lat: float, lon: float, timeout=30) -> list[dict]:  # pragma: no cover - network
    """Free EU JRC PVGIS TMY service (no key). Requires outbound internet."""
    import json, urllib.request
    url = f"https://re.jrc.ec.europa.eu/api/v5_3/tmy?lat={lat}&lon={lon}&outputformat=json"
    with urllib.request.urlopen(url, timeout=timeout) as r:
        data = json.load(r)
    rows = []
    for h in data["outputs"]["tmy_hourly"]:
        t = datetime.strptime(h["time(UTC)"], "%Y%m%d:%H%M").replace(tzinfo=timezone.utc)
        rows.append({"time": t.replace(year=2026), "ghi": h["G(h)"], "dni": h["Gb(n)"],
                     "dhi": h["Gd(h)"], "temp_air": h["T2m"]})
    return rows
