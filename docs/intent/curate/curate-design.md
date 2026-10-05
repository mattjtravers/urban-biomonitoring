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
    # Implementation: NWS API, GET https://api.weather.gov/stations/{station_id}/observations
    # (no API key; a User-Agent identifying the project is required).

class SummaryBuilder(Protocol):
    def daily(self, detections: Table, calibrations: Table, audio: Table, *,
              run_id: str, analysis_timezone: str) -> list[DailySummary]: ...
```

Rules the builder will follow (to become CURATE specs):

- Use detections from one chosen run per `model_id`, exclude `overlaps_mask = true` against
  the **current** mask version, and exclude `is_target_taxon = false` (those become
  NoiseEvents instead).
- A taxon with a calibration uses `threshold_at_target`. Without one it's reported with
  `validation_status = "unvalidated"` and no calibrated counts.
- `recorded_minutes` comes from archived audio durations, so effort is explicit for
  comparisons between sites and days.
- `date_local` uses `analysis_timezone` (default `America/New_York`) for diel and phenology
  views. Stored timestamps stay UTC.

New analyses (soundscape indices, eBird comparison, land cover) are added as new curate tables,
not core changes (HLD §7).

## Decisions & Alternatives

| Decision | Chosen | Alternatives Considered | Rationale |
|---|---|---|---|
| Weather source | NWS observations API | Meteostat; Open-Meteo reanalysis | The HLD names NWS. It's free, official, and station-based. |
| Summary time zone | Local analysis time zone for dates; UTC for timestamps | UTC dates | Diel and phenology questions are about local days. |

## Open Questions & Future Decisions

### Deferred
1. All CURATE specs, until calibrations exist.
2. Weekly summary definition (ISO week vs. BirdNET week).

## References

- HLD §1.2, §1.3, §3.5, §7
- NWS API: https://www.weather.gov/documentation/services-web-api
