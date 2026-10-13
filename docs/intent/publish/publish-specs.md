# Publish Specs

EARS requirements for [publish](publish-design.md). Markers: `[x]` implemented, `[ ]` active
gap, `[D]` deferred. Publish is interfaces and policy only in Phase 1, so its specs are deferred
until curate produces summaries.

## Release Gate

- [D] **PUB-GATE-001**: When publish builds a release, the system shall read only current rows (RUN-CURRENT-001), so flagged and failed units are excluded unit by unit, and the release notes shall count the excluded units.
- [D] **PUB-GATE-002**: If any current row publish would use comes from a run with `git_dirty: true`, then the system shall refuse to publish, in any profile.

## Allowlist

- [D] **PUB-COL-001**: The system shall publish only allowlisted tables and columns, and never `original_filename`, `archive_key`, `sidecar_key`, device serials, recorder names, or anything from sidecars or device logs.
- [D] **PUB-COL-002**: The system shall publish site coordinates only as the generalized values, rounded to `publish.coordinate_precision_deg` (0.01° by default, with per-site overrides only coarser), with `coordinateUncertaintyInMeters` and `dataGeneralizations`.

## Darwin Core

- [D] **PUB-DWC-001**: When publish exports a Darwin Core Archive, the system shall map fields as in publish § Darwin Core Mapping, with `occurrenceID` `urn:urbanbio:<detection_id>`, `basisOfRecord` `MachineObservation`, and `identifiedBy` the model ID, never a person.
- [D] **PUB-DWC-002**: The system shall license published data CC BY 4.0.

## Clips

- [D] **PUB-CLIP-001**: When publish selects clips, the system shall take candidates only from `detections_current` of calibrated taxa, never with `overlaps_current_mask` true, and publish only live clips (no matching `clip_withdrawals` row).
- [D] **PUB-CLIP-002**: When publish re-checks a clip, the system shall re-screen it with both speech detectors at stricter thresholds than the archive screen and reject it on any hit.
- [D] **PUB-CLIP-003**: The system shall publish clips with no metadata tags.

## Hosting

- [D] **PUB-HOST-001**: The system shall serve published material only from GitHub Pages, never from the store.
