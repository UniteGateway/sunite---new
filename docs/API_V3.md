# /api/v3 request structure
`POST /api/v3/design`, `/api/v3/export/report.pdf`, `/api/v3/export/boq.xlsx` all take:
```json
{
  "site": {"name": "...", "latitude": 17.385, "longitude": 78.487, "north_rotation_deg": 0,
           "parapet_height_m": 0.9, "roof": [[0,0],[30,0],[30,18],[0,18]],
           "obstacles": [{"name": "Stair", "polygon": [[8,7],[12,7],[12,10],[8,10]], "height_m": 3,
                          "kind": "obstacle|keepout|tree|building", "transmittance": 0, "clearance_m": null}]},
  "module": {"model": "...", "pmax_w": 550, "voc_v": 49.9, "vmp_v": 41.7, "isc_a": 14.0, "imp_a": 13.19,
             "beta_voc_pct": -0.28, "gamma_pmax_pct": -0.35, "alpha_isc_pct": 0.05, "beta_vmp_pct": null,
             "max_system_v": 1500, "length_m": 2.278, "width_m": 1.134, "verified": false},
  "layout": {"orientation": "portrait", "tilt_deg": 10, "array_azimuth_deg": 180, "setback_m": 0.5,
             "obstacle_clearance_m": 0.6, "module_gap_m": 0.02, "min_row_gap_m": 0.25, "mount_height_m": 0.3,
             "spacing_rule": "solstice_9_15|fixed", "optimize_offsets": 3},
  "inverter": {"model": "...", "ac_kw": 60, "max_dc_v": 1100, "mppt_min_v": 200, "mppt_max_v": 1000,
               "mppt_count": 4, "strings_per_mppt": 2, "max_input_current_per_mppt_a": 30,
               "max_isc_per_mppt_a": 40, "verified": false},
  "temps": {"min_ambient_c": 5, "max_cell_c": 70},
  "shading": {"step_min": 60, "nu": 3, "nv": 3, "include_row_shading": true},
  "cables": {"inverter_xy": [0, 0], "dc_route_factor": 1.3, "dc_vd_limit_pct": 1.5, "ampacity_table": {"4": 0}},
  "pricing": {"rates": {"PV-MOD": 0}, "tax_pct_by_category": {"material": 0, "service": 0},
              "wastage_pct": {"DC-CAB": 3}, "freight_pct": 0, "contingency_pct": 0},
  "finance": {"tariff_per_kwh": 0, "degradation_pct": 0.5, "tariff_escalation_pct": 0, "om_year1": 0,
              "om_escalation_pct": 0, "discount_rate_pct": 0},
  "weather_source": "pvgis", "weather_csv": "time_utc,ghi,dni,dhi,temp_air\n..."
}
```
Coordinates are metres; `north_rotation_deg` is the bearing of the drawing's +y axis from true north.
All numbers above are illustrative placeholders; tariffs, taxes and rates must be supplied with source dates.
The legacy v0.2 routes (`/api/design`, `/api/projects`, `/api/export/*`) are unchanged and now use the v0.3 engine.
