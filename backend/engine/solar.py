"""Solar position (NOAA General Solar Position algorithm, Julian-century form).

Accuracy is ~0.01 deg for 1950-2050 per NOAA; atmospheric refraction is applied
for apparent elevation. Azimuth is degrees clockwise from true north.
pvlib's SPA is preferred where installed; this module needs no dependencies and is
benchmarked in tests against physical invariants (solstice declination,
equation-of-time extremes, solar-noon elevation).
"""
from __future__ import annotations
import math
from dataclasses import dataclass
from datetime import datetime, timezone, timedelta


@dataclass
class SunPos:
    elevation: float       # apparent (refraction-corrected), degrees
    azimuth: float         # degrees clockwise from north
    declination: float     # degrees
    eot_min: float         # equation of time, minutes
    zenith: float


def _julian_day(dt: datetime) -> float:
    if dt.tzinfo is None:
        raise ValueError("datetime must be timezone-aware")
    dt = dt.astimezone(timezone.utc)
    return dt.timestamp() / 86400.0 + 2440587.5


def sun_position(dt: datetime, lat: float, lon: float) -> SunPos:
    jd = _julian_day(dt)
    t = (jd - 2451545.0) / 36525.0
    l0 = (280.46646 + t * (36000.76983 + t * 0.0003032)) % 360
    m = 357.52911 + t * (35999.05029 - 0.0001537 * t)
    e = 0.016708634 - t * (0.000042037 + 0.0000001267 * t)
    mr = math.radians(m)
    c = (math.sin(mr) * (1.914602 - t * (0.004817 + 0.000014 * t))
         + math.sin(2 * mr) * (0.019993 - 0.000101 * t) + math.sin(3 * mr) * 0.000289)
    true_long = l0 + c
    omega = 125.04 - 1934.136 * t
    app_long = true_long - 0.00569 - 0.00478 * math.sin(math.radians(omega))
    eps0 = 23 + (26 + (21.448 - t * (46.815 + t * (0.00059 - t * 0.001813))) / 60) / 60
    eps = eps0 + 0.00256 * math.cos(math.radians(omega))
    epsr = math.radians(eps)
    decl = math.degrees(math.asin(math.sin(epsr) * math.sin(math.radians(app_long))))
    y = math.tan(epsr / 2) ** 2
    l0r = math.radians(l0)
    eot = 4 * math.degrees(y * math.sin(2 * l0r) - 2 * e * math.sin(mr)
                           + 4 * e * y * math.sin(mr) * math.cos(2 * l0r)
                           - 0.5 * y * y * math.sin(4 * l0r) - 1.25 * e * e * math.sin(2 * mr))
    utc = dt.astimezone(timezone.utc)
    minutes = utc.hour * 60 + utc.minute + utc.second / 60
    tst = (minutes + eot + 4 * lon) % 1440
    ha = tst / 4 - 180
    latr, dr, har = math.radians(lat), math.radians(decl), math.radians(ha)
    cosz = math.sin(latr) * math.sin(dr) + math.cos(latr) * math.cos(dr) * math.cos(har)
    zen = math.degrees(math.acos(max(-1, min(1, cosz))))
    elev = 90 - zen
    # NOAA refraction approximation
    if elev > 85:
        refr = 0
    else:
        te = math.tan(math.radians(elev)) if abs(elev) > 1e-6 else 1e-6
        if elev > 5:
            refr = 58.1 / te - 0.07 / te ** 3 + 0.000086 / te ** 5
        elif elev > -0.575:
            refr = 1735 + elev * (-518.2 + elev * (103.4 + elev * (-12.79 + elev * 0.711)))
        else:
            refr = -20.772 / te
        refr /= 3600
    app_elev = elev + refr
    zr = math.radians(zen)
    if abs(math.sin(zr)) < 1e-9:
        az = 180.0
    else:
        caz = (math.sin(latr) * math.cos(zr) - math.sin(dr)) / (math.cos(latr) * math.sin(zr))
        az = math.degrees(math.acos(max(-1, min(1, caz))))
        az = (az + 180) % 360 if ha > 0 else (540 - az) % 360
    return SunPos(app_elev, az, decl, eot, 90 - app_elev)


def solar_noon_utc(date: datetime, lon: float) -> datetime:
    """UTC instant of local solar noon for the calendar date of `date`."""
    base = datetime(date.year, date.month, date.day, 12, tzinfo=timezone.utc)
    eot = sun_position(base, 0, lon).eot_min
    return base + timedelta(minutes=-4 * lon - eot)


def sun_vector(elevation: float, azimuth: float) -> tuple[float, float, float]:
    """Unit vector pointing TO the sun in (east, north, up)."""
    e, a = math.radians(elevation), math.radians(azimuth)
    return (math.cos(e) * math.sin(a), math.cos(e) * math.cos(a), math.sin(e))


def daylight_samples(lat: float, lon: float, year: int = 2026, step_min: int = 60,
                     min_elev: float = 5.0, day_of_month: int = 15):
    """Representative day per month, samples from solar noon outward while sun is up.

    Times are anchored to *local solar time*, so the sampling is correct for any
    longitude (fixes v0.2, which hard-coded UTC 03-10h, valid only near India)."""
    out = []
    for month in range(1, 13):
        noon = solar_noon_utc(datetime(year, month, day_of_month, tzinfo=timezone.utc), lon)
        for k in range(-12 * 60 // step_min, 12 * 60 // step_min + 1):
            t = noon + timedelta(minutes=k * step_min)
            sp = sun_position(t, lat, lon)
            if sp.elevation >= min_elev:
                out.append((t, sp))
    return out


def sun_path_day(lat: float, lon: float, date_iso: str, step_min: int = 30, min_elev: float = 0.0):
    """Sun positions through one day, anchored to local solar noon (for animation and reports)."""
    d = datetime.fromisoformat(date_iso).replace(tzinfo=timezone.utc)
    noon = solar_noon_utc(d, lon)
    out = []
    for k in range(-14 * 60 // step_min, 14 * 60 // step_min + 1):
        t = noon + timedelta(minutes=k * step_min)
        sp = sun_position(t, lat, lon)
        if sp.elevation >= min_elev:
            out.append({"utc": t.isoformat(), "solar_minutes_from_noon": k * step_min,
                        "elevation_deg": round(sp.elevation, 3), "azimuth_deg": round(sp.azimuth, 3)})
    return out
