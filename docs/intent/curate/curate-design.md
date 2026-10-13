---
parent: high-level-design
prefix: CURATE
---

# Curate

## Context and Design Philosophy

Curate builds the analysis tables (HLD §3.5): it joins weather covariates, derives noise
events, and computes daily and weekly summaries from calibrated detections, recording which
calibration each summary used. Outputs measure **vocal activity**, never abundance (HLD §1.2),
and every summary column is named to say so.

**Phase 1 scope: interfaces only.** Curate depends on validate's calibrations. The core
tables it reads (`audio_files`, `audio_mask_versions`, `detections`, `speech_events`) are fully
specified in Phase 1 by [data-model](../data-model/data-model-design.md).

Package: `urbanbio.curate`.

## Interfaces

```python
class WeatherSource(Protocol):
    def hourly(self, station_id: str, start_utc: datetime, end_utc: datetime) -> list[WeatherObs]: ...
    # Implementation: NOAA NCEI Global Hourly (Integrated Surface Database), which archives the
    # hourly observations that NWS stations (ASOS/AWOS) report, with full history.

class SummaryBuilder(Protocol):
    def daily(self, detections: Table, calibrations: Table, audio: Table, sites: Table, *,
              run_id: str) -> list[DailySummary]: ...
```

Rules the builder will follow (to become CURATE specs):

- Read `detections_current` for the chosen `model_id` (current rows per unit; see
  [runs](../runs/runs-design.md) § Current Rows), exclude `overlaps_current_mask = true`, and
  exclude `is_target_taxon = false` (those become NoiseEvents instead).
- A taxon with a calibration uses `threshold_at_target`. Without one it's reported with
  `validation_status = "unvalidated"` and no calibrated counts.
- `recorded_minutes` comes from archived audio durations, so effort is explicit for
  comparisons between sites and days.
- `date_local` and diel hours use each site's `timezone`
  ([data-model](../data-model/data-model-design.md) § Site), with the zone's daylight-saving
  rules, for diel and phenology views. Stored timestamps stay UTC.

New analyses (soundscape indices, eBird comparison, land cover) are added as new curate tables,
not core changes (HLD §7).

## Decisions & Alternatives

| Decision | Chosen | Alternatives Considered | Rationale |
|---|---|---|---|
| Weather source | NCEI Global Hourly (ISD) for NWS station observations | NWS observations API (`api.weather.gov`); Meteostat; Open-Meteo reanalysis | The HLD names NWS station observations, and NCEI is their official archive. The `api.weather.gov` observations endpoint serves only recent observations, so it can't backfill the months before curate first runs. |
| Summary time zone | Each site's IANA zone for dates; UTC for timestamps | UTC dates; one project-wide zone | Diel and phenology questions are about local days, and the zone belongs to the site. |

## Open Questions & Future Decisions

### Deferred
1. All CURATE specs, until calibrations exist.
2. Weekly summary definition (ISO week vs. BirdNET week).
3. **TODO: verify before curate specs:** how a site's `nws_station_id` (ICAO, e.g. `KXYZ`) maps
   to its ISD station identifier (USAF-WBAN), and the retention of the `api.weather.gov`
   observations endpoint.

## References

- HLD §1.2, §1.3, §3.5, §7
- NWS API: https://www.weather.gov/documentation/services-web-api
- NCEI Global Hourly (ISD): https://www.ncei.noaa.gov/products/land-based-station/integrated-surface-database
