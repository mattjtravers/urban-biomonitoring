---
parent: high-level-design
prefix: PUB
---

# Publish

## Context and Design Philosophy

Publish is the only stage whose output is public (HLD §3.6). Everything it emits goes through
an allowlist: columns, files, and clips are published only when explicitly selected, so
anything added to the private tables stays private unless someone deliberately adds it here.
Public material is served from GitHub Pages only. The store stays private
([store](../store/store-design.md)).

**Phase 1 scope: interfaces and policy only.** PUB specs are deferred until curate produces
summaries.

Package: `urbanbio.publish`.

## Interfaces

```python
class Exporter(Protocol):
    def parquet(self, tables: Mapping[str, Table], out_dir: Path) -> list[Path]: ...   # allowlisted columns
    def dwca(self, occurrences: Table, sites: Table, out_dir: Path) -> Path: ...      # Darwin Core Archive zip

class ClipPublisher(Protocol):
    def candidates(self, detections: Table) -> Table: ...      # never overlaps_mask; calibrated taxa only
    def recheck(self, clip: MaskedClip) -> SpeechCheckResult: ...  # second speech check (HLD §3.6)
    def withdraw(self, detection_ids: Sequence[str]) -> None: ...  # after re-masking

class SiteBuilder(Protocol):
    def build(self, export_dir: Path, out_dir: Path) -> Path: ...  # static site with DuckDB-WASM
```

Policy the specs will encode:

- **Release gate.** Publish refuses inputs from runs that are `partial`, `failed`, or
  `git_dirty` ([runs](../runs/runs-design.md)).
- **Columns.** Public tables never include `original_filename`, `archive_key`, `sidecar_key`,
  device serials, recorder names, or anything from sidecars or device logs.
- **Clip re-check.** Each clip is re-screened with both speech detectors at stricter
  thresholds than the archive screen (proposed: BirdNET `Human vocal` ≥ 0.05 at 0.5 s hop;
  Silero ≥ 0.20). Any hit rejects the clip. Clips carry no metadata tags.
- **Withdrawal.** A re-mask that touches a published clip's interval withdraws the clip in the
  next release and deletes it from `clips/` (the pipeline's only delete permission).

## Darwin Core Mapping

| DwC term | Value |
|---|---|
| `occurrenceID` | `urn:urbanbio:<detection_id>` (calibrated, above threshold; stable across re-runs of the same model, see [data-model](../data-model/data-model-design.md) § Identifiers) or an aggregated daily-presence ID |
| `basisOfRecord` | `MachineObservation` |
| `eventDate` | ISO 8601 UTC |
| `scientificName` | From the model label |
| `identificationVerificationStatus` | `calibrated: P(correct) ≥ <target>` or `unvalidated` |
| `decimalLatitude`, `decimalLongitude` | Generalized site coordinates |
| `coordinateUncertaintyInMeters` | Site value (default 1000) |
| `dataGeneralizations` | e.g. "Coordinates rounded to 0.01 degrees" |
| `locationID` | `site<N>` |
| `identifiedBy` | Model ID (e.g. `BirdNET V2.4 (birdnet 1.1.1)`), never a person |

## T4 Resolution (HLD §10): Published Coordinate Precision

**0.01°**, declared as `coordinateUncertaintyInMeters = 1000` and
`dataGeneralizations = "Coordinates rounded to 0.01 degrees"`. Rounding to 0.01° moves a point by
at most 0.005° in each axis: about 556 m north–south, and about 426 m east–west at 40° N, for a
worst case near 700 m. Declaring 1000 m covers that plus device and GPS error. Per-site
overrides may only be coarser (e.g. 0.05° for a site where a 1 km cell contains few buildings).

## T6 Resolution (HLD §10): Published Data License

**CC BY 4.0**, one of the licenses GBIF accepts (with CC0 1.0 and CC BY-NC 4.0), so GBIF
publication stays open (HLD §4). Before the first release, confirm that the BirdNET models'
CC BY-NC-SA 4.0 license places no conditions on published detection data. If it does, CC BY-NC
4.0 is the GBIF-compatible fallback.

## Decisions & Alternatives

| Decision | Chosen | Alternatives Considered | Rationale |
|---|---|---|---|
| Hosting | GitHub Pages | A public store prefix | No public store surface; no egress charges for downloads. |
| Output control | Allowlist | Denylist of private columns | A new private column is private by default. |
| Clip check | Stricter second screen; reject on any hit | Reuse archive screen results | HLD §3.6 requires a second check. Clips are short and public, so false rejections are cheap. |

## Open Questions & Future Decisions

### Deferred
1. **How Pages gets built without committing audio.** This repository's hygiene rule forbids
   committing audio, and published clips are audio. Options: deploy Pages from a workflow
   artifact (`actions/upload-pages-artifact` + `actions/deploy-pages`) built from a release
   bundle; publish clips from a separate public repository; or publish no clips at first.
   Decide before the first release.
2. GitHub Pages limits (published site size 1 GB; soft bandwidth limit of 100 GB/month) bound
   the clip count and Parquet size. Check against the first release.
3. All PUB specs.

## References

- HLD §3.6, §4 (Standards), §6.2, T4, T6
- Darwin Core: https://dwc.tdwg.org/terms/
- GBIF licensing (CC0, CC BY, CC BY-NC; version 4.0 recommended): https://ipt.gbif.org/manual/en/ipt/2.5/applying-license
- DuckDB-WASM: https://duckdb.org/docs/api/wasm/overview
- GitHub Pages limits: https://docs.github.com/en/pages/getting-started-with-github-pages/github-pages-limits
