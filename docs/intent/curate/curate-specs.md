# Curate Specs

EARS requirements for [curate](curate-design.md). Markers: `[x]` implemented, `[ ]` active gap,
`[D]` deferred. Curate is interfaces only in Phase 1, so its specs are deferred until
calibrations exist.

## Daily Summaries

- [D] **CURATE-SUM-001**: When the summary builder runs, the system shall read `detections_current` for the chosen `model_id`, excluding rows with `overlaps_current_mask` true and rows with `is_target_taxon` false.
- [D] **CURATE-SUM-002**: When a taxon has a calibration, the summary builder shall count detections at or above its `threshold_at_target`; when it has none, it shall report the taxon with `validation_status = "unvalidated"` and no calibrated counts.
- [D] **CURATE-SUM-003**: The summary builder shall compute `recorded_minutes` from archived audio durations.
- [D] **CURATE-SUM-004**: The summary builder shall compute `date_local` and diel hours in each site's `timezone`, with that zone's daylight-saving rules, keeping stored timestamps in UTC.
- [D] **CURATE-SUM-005**: The summary builder shall name every summary column as a measure of vocal activity, never abundance.

## Covariates

- [D] **CURATE-WX-001**: When the system fetches weather, it shall read hourly observations for each site's station from NOAA NCEI Global Hourly (ISD).
- [D] **CURATE-NOISE-001**: When curate builds NoiseEvents, the system shall derive them from current detections with `is_target_taxon` false.
