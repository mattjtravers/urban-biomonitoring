---
parent: high-level-design
prefix: DETECT
---

# Detect

## Context and Design Philosophy

The detect stage runs a species classifier over archived, speech-masked audio and stores
**every** output above a low floor, with the model, version, and parameters that produced it
(HLD §3.3). It doesn't decide what is "real": thresholds come later from per-species
calibration ([validate](../validate/validate-design.md)). Detect's job is to be complete,
reproducible, and comparable across model versions.

The classifier sits behind a `Detector` interface, so a new model or model version is a new
run in its own partition and never overwrites earlier results (HLD §3.3, Tenet 2).

Package: `urbanbio.detect`.

## Detector Interface

```python
class Detector(Protocol):
    model_id: str                    # e.g. "birdnet-acoustic-2.4-fp32"; partition key
    def describe(self) -> ModelInfo: ...         # family, version, backend, precision, file SHA-256s
    def params(self) -> dict[str, object]: ...   # everything that affects outputs
    def labels(self) -> list[str]: ...
    def is_target(self, label: str) -> bool: ... # False for non-taxon classes
    def detect(self, inputs: Sequence[AnalysisInput]) -> Iterator[RawDetection]: ...

class GeoFilter(Protocol):
    model_id: str                    # e.g. "birdnet-geo-2.4"
    def species(self, latitude_deg: float, longitude_deg: float, week: int) -> frozenset[str]: ...
```

- `AnalysisInput`: `audio_file_id`, `mask_version`, `path` (a local masked FLAC whose SHA-256
  has been checked against `masked_sha256`).
- `RawDetection`: `audio_file_id`, `start_s`, `end_s`, `label`, `confidence`.

The stage, not the detector, attaches IDs, UTC times, the geo flag, and the mask-overlap flag,
so every detector gets the same treatment.

## BirdNET Implementation

| Item | Value |
|---|---|
| Library | `birdnet==1.1.1` (PyPI; MIT; requires Python ≥ 3.11) |
| Acoustic model | BirdNET V2.4 (6,522 classes), `birdnet.load("acoustic", "2.4", "tf", library="litert")` |
| Runtime | LiteRT (`ai-edge-litert`, installed with `birdnet` on Linux for Python 3.11–3.13). No TensorFlow. |
| Precision | FP32 |
| Model license | CC BY-NC-SA 4.0. The library downloads models at first use into `BIRDNET_APP_DATA` (`paths.model_cache`); they're never committed or vendored (HLD §8). |
| Python | 3.13 (the newest version with LiteRT wheels) |
| `model_id` | `birdnet-acoustic-2.4-fp32` |

Prediction settings. Each configurable value is a key in `[detect]` of `config/pipeline.toml`
and so part of the stage's `params_hash`:

| Config key | Library parameter | Value | Note |
|---|---|---|---|
| `confidence_floor` | `default_confidence_threshold` | 0.10 | The stored floor (T2) |
| `top_k` | `top_k` | `"none"` (library `None`) | Every class above the floor, not just the top 5 |
| `overlap_s` | `overlap_duration_s` | 0.0 | Contiguous 3 s windows: 20 per 1-minute file |
| `sigmoid_sensitivity` | `sigmoid_sensitivity` | 1.0 | Model default; calibration works on these scores |
| `bandpass_fmin_hz`, `bandpass_fmax_hz` | `bandpass_fmin`, `bandpass_fmax` | 0, 15,000 Hz | Model defaults |
| — | `custom_species_list` | `None` (fixed) | The geo filter is applied as a flag, below |

Results come back as an Arrow table (`input`, `start_time`, `end_time`, `species_name`,
`confidence`, with model metadata in the schema). The stage maps it to `Detection` rows.
`species_name` has the form `<scientific name>_<common name>` and is split at the first `_`.

**Non-target classes.** V2.4 includes 11 non-taxon classes: `Dog`, `Engine`, `Environmental`,
`Fireworks`, `Gun`, `Human non-vocal`, `Human vocal`, `Human whistle`, `Noise`, `Power tools`,
and `Siren` (each labeled `<X>_<X>`). They're stored with `is_target_taxon = false` as noise
covariates (HLD §3.3) and become NoiseEvents in [curate](../curate/curate-design.md).

## Location and Date Filtering

BirdNET's own filter drops every class not in the location/week species list, including the
non-target classes the HLD requires. Instead of filtering, detect **flags**:

- The geo model (`birdnet.load("geo", "2.4", "tf", library="litert")`, `model_id
  birdnet-geo-2.4`) gives the species expected at a location in a week, with
  `min_confidence = 0.03` (the BirdNET default occurrence threshold).
- Inputs: the site's **generalized** coordinates (exact coordinates never reach this stage) and
  the BirdNET week, from the UTC date of the file start. BirdNET defines the week only as
  1–48, "4 weeks per month" (BirdNET Analyzer CLI help). This project maps days with
  `week = 4 × (month − 1) + min(4, ⌈day / 7⌉)`.
- Each run writes the lists it used to `geo_species_lists` (`site_id`, `week`, `label`,
  `occurrence_score`, `run_id`), and each detection gets `in_geo_list`.
- Non-target classes are never in the geo list. Downstream code uses `is_target_taxon` to tell
  them apart.

Flagging keeps the filter reversible: changing the geo threshold re-flags without re-running
the acoustic model.

## Detect Flow

`urbanbio detect --retrieval R` or `--all-pending`:

1. Units: every (file, `model_id`) whose detect idempotency key has no `succeeded` row
   ([runs](../runs/runs-design.md)). The key's inputs are the file's `original_pcm_sha256`, not
   the masked checksum, with models = acoustic and geo model file hashes and params. A re-mask
   therefore doesn't make a file pending: it only zeroes windows that `overlaps_current_mask`
   already excludes, and the windows outside it hold identical samples. A unit that does run
   analyzes the file's current mask version and records it in `mask_version`.
2. For each batch of up to 50 units ([runs](../runs/runs-design.md) § Write Order), taken in
   order of deployment and file start: use the work-directory FLAC left by archive
   if its SHA-256 matches `masked_sha256`; otherwise fetch it from the store to the work directory
   with `store.get`, which verifies it. Then call `detect` with the batch's file paths and `n_workers` set to
   `resources.workers` ([runs](../runs/runs-design.md) § Resource Limits). The library's
   workers each hold one loaded model and read one file at a time, so memory doesn't grow
   with the batch.
3. Attach `detection_id`, `start_utc`, `site_id`, `deployment_id`, `mask_version`,
   `in_geo_list`, and `overlaps_mask_at_detect` (the window intersects a masked interval of the
   mask version analyzed).
4. Write the batch's `detections` part files (partitioned by `model_id`, `site_id`, and
   `year_month`), then its ledger part, then delete the batch's work-directory FLACs
   ([runs](../runs/runs-design.md) § Write Order).

A file whose checksum doesn't match `masked_sha256` is flagged `archive_conflict` (Tenet 3),
except an interrupted re-mask (the object's `mask-version` is one above the recorded version and
its `run-id` names an earlier `rescreen` run; [archive](../archive/archive-design.md)
§ Re-masking), which marks the unit
`failed` (`remask_in_progress`) so it is retried once `rescreen` has finished it
([archive](../archive/archive-design.md) § Re-masking). A file that fails to decode, or a model
error, marks the unit `failed`, and the next run retries it.

**Volume.** At a 0.10 floor with `top_k = None`, a 3 s window yields a few classes on average.
The expected order is 10⁴ rows per recorder-day, so about 10⁷ rows and on the order of 100 MB
of Parquet a year for two recorders, small for DuckDB.

**Compute.** The library's published benchmark is 50× real time on a 4-core Intel i7 (8th
generation). With the default 2 workers on a host of similar speed, a week of two recorders
(67 hours of audio) is expected to take about 1–3 hours. **TODO: measure on the first real card**:
wall-clock time and peak memory.

## Re-runs and New Models

- A new BirdNET version, a different backend or precision, or another family (Perch V2,
  BirdNET V3.0 through the same library) is a new `model_id`. It writes its own partition, and
  earlier partitions are untouched.
- Re-running the same model after a parameter change produces new detections under a new
  `run_id` in the same `model_id` partition. A re-mask doesn't re-run detect (step 1).
- **Current detections** are chosen per unit, not per run: for each file and `model_id`, the
  rows of the latest succeeded run on that file, whichever mask version it analyzed
  ([runs](../runs/runs-design.md) § Current Rows). The `detections_current` view applies this
  and adds `overlaps_current_mask` ([data-model](../data-model/data-model-design.md)
  § Detection), which excludes windows a later mask covers. Queries that need an older run
  select it by `run_id`.
- Reprocessing older audio reads Glacier IR objects directly (no restore). Cost and pacing
  are covered in [archive](../archive/archive-design.md) § Cost.

## Decisions & Alternatives

| Decision | Chosen | Alternatives Considered | Rationale |
|---|---|---|---|
| Integration path | `birdnet` library 1.1.1 on LiteRT | BirdNET Analyzer 2.4.0 CLI/API (TensorFlow ≥ 2.20, CSV output) | Same team and V2.4 model. In-memory Arrow results instead of CSV files, no multi-GB TensorFlow install, active releases (2026), and the same API also serves V3.0 and Perch V2 later. |
| Model version | V2.4 | V3.0 (marked preview by the library) | V2.4 is the stable release with the published calibration literature (HLD §3.4). V3.0 can run later as a second `model_id`. |
| Stored floor (T2) | 0.10 | 0.25 (library/Analyzer default); 0.01 | Calibration needs low-confidence detections to fit the logistic curve. 0.01 would grow the table about tenfold for scores that almost never pass review. |
| Location/date filter | Flag (`in_geo_list`) | Filter at prediction time (`custom_species_list`) | Filtering would drop the non-target noise classes (HLD §3.3) and make the filter irreversible. |
| Window overlap | 0 s | 1.5 s | Non-overlapping windows keep counts simple (each 3 s of audio is scored once). Overlap is used in the speech screen, where recall matters more. |
| Input | Archived masked FLAC | Unmasked quarantine audio | HLD §3.3: detect runs on masked audio, so results reproduce exactly from the archive. |
| Re-detect after a re-mask | No; covered windows excluded by `overlaps_current_mask` | Re-run detect on every new mask version | Outside the new mask the samples are identical, so a re-run reproduces the same detections at the cost of Glacier IR retrieval and transfer, and leaves the file without current detections until it runs. |

## Open Questions & Future Decisions

### Deferred
1. Throughput on a processing host at the default worker count. If too slow, reuse the screen's BirdNET outputs for windows
   untouched by the mask
   ([speech-screen](../speech-screen/speech-screen-design.md) § Deferred), or run detect on a
   larger host against the same store.
2. Whether the BirdNET model's CC BY-NC-SA 4.0 terms carry any conditions onto published
   detection data. This bears on T6 in [publish](../publish/publish-design.md).

## References

- HLD §3.3, §7, §8, T2, Tenet 2
- `birdnet` library: https://github.com/birdnet-team/birdnet (v1.1.1, 2026-09-02),
  documentation https://birdnet-team.github.io/birdnet, PyPI https://pypi.org/project/birdnet/
- BirdNET Analyzer (reference implementation, V2.4 labels and defaults):
  https://github.com/birdnet-team/BirdNET-Analyzer (v2.4.0, 2025-11-07)
- Kahl, S., Wood, C. M., Eibl, M., & Klinck, H. (2021). BirdNET: A deep learning solution for
  avian diversity monitoring. *Ecological Informatics* 61:101236.
