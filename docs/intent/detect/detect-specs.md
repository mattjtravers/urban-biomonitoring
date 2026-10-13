# Detect Specs

EARS requirements for [detect](detect-design.md). Markers: `[x]` implemented, `[ ]` active gap,
`[D]` deferred.

## Detector Interface

- [ ] **DETECT-IF-001**: The system shall run species classifiers only through the `Detector` protocol (`model_id`, `describe`, `params`, `labels`, `is_target`, `detect`) and location filters only through the `GeoFilter` protocol (`model_id`, `species`).
- [ ] **DETECT-IF-002**: The detect stage, not the detector, shall attach `detection_id`, `start_utc`, `site_id`, `deployment_id`, `mask_version`, `in_geo_list`, and `overlaps_mask_at_detect` to each `RawDetection`.
- [ ] **DETECT-IF-003**: The system shall pass a detector only `AnalysisInput`s whose local masked FLAC has a SHA-256 equal to the file's current `masked_sha256`.

## BirdNET Implementation

- [ ] **DETECT-BN-001**: Where `detect.model` is `birdnet-acoustic-2.4` with backend `litert` and precision `fp32`, the system shall load the model with `birdnet.load("acoustic", "2.4", "tf", library="litert")` and use `model_id` `birdnet-acoustic-2.4-fp32`.
- [ ] **DETECT-BN-002**: When the BirdNET detector predicts, the system shall pass `default_confidence_threshold = detect.confidence_floor`, `top_k = None` when `detect.top_k` is `"none"`, `overlap_duration_s = detect.overlap_s`, `sigmoid_sensitivity = detect.sigmoid_sensitivity`, `bandpass_fmin = detect.bandpass_fmin_hz`, `bandpass_fmax = detect.bandpass_fmax_hz`, and `custom_species_list = None`.
- [ ] **DETECT-BN-003**: The system shall set the BirdNET model cache to `paths.model_cache` through `BIRDNET_APP_DATA`, and never commit or vendor model files.
- [ ] **DETECT-BN-004**: When the system maps a BirdNET result to a `Detection`, it shall split `species_name` at the first `_` into `scientific_name` and `common_name`, keeping `species_name` as `label`.
- [ ] **DETECT-BN-005**: The BirdNET detector's `is_target` shall return false for the 11 V2.4 non-taxon classes (`Dog`, `Engine`, `Environmental`, `Fireworks`, `Gun`, `Human non-vocal`, `Human vocal`, `Human whistle`, `Noise`, `Power tools`, `Siren`) and true otherwise, and the system shall store their detections with `is_target_taxon = false`.

## Location and Date Flag

- [ ] **DETECT-GEO-001**: When the detect stage evaluates the location filter, the system shall load the geo model with `birdnet.load("geo", "2.4", "tf", library="litert")` (`model_id` `birdnet-geo-2.4`) and query it with `min_confidence = detect.geo_min_confidence` at the site's generalized coordinates.
- [ ] **DETECT-GEO-002**: The system shall compute the BirdNET week from the UTC date of the file start as `4 × (month − 1) + min(4, ⌈day / 7⌉)`.
- [ ] **DETECT-GEO-003**: When a detect run uses a species list, the system shall write it to `geo_species_lists` with `site_id`, `week`, `label`, `occurrence_score`, and `run_id`.
- [ ] **DETECT-GEO-004**: The system shall set each detection's `in_geo_list` to whether its label is in the site-and-week species list, and never drop a detection for being outside it.

## Detect Flow

- [ ] **DETECT-FLOW-001**: When `urbanbio detect --retrieval R` or `--all-pending` runs, the system shall take as units every archived (file, `model_id`) whose detect idempotency key, built from the file's `original_pcm_sha256`, the acoustic and geo model file hashes, and the detect parameters, has no `succeeded` ledger row.
- [ ] **DETECT-FLOW-002**: When a detect unit runs, the system shall analyze the file's current mask version and record that version in each detection's `mask_version`.
- [ ] **DETECT-FLOW-003**: When the detect stage prepares a batch of units, taken in order of deployment and file start, the system shall use the work-directory FLAC when its SHA-256 equals `masked_sha256`, and otherwise fetch the object with `store.get` into the work directory.
- [ ] **DETECT-FLOW-004**: When the detect stage runs a batch, the system shall call the detector with the batch's file paths and `n_workers = resources.workers`.
- [ ] **DETECT-FLOW-005**: When the detect stage records a detection, the system shall set `overlaps_mask_at_detect` to whether its window intersects a masked interval of the mask version analyzed.
- [ ] **DETECT-FLOW-006**: When a detect batch finishes, the system shall write its `detections` part files partitioned by `model_id`, `site_id`, and `year_month`, then its ledger part, then delete the batch's work-directory FLACs.
- [ ] **DETECT-FLOW-007**: If a fetched FLAC's checksum doesn't match `masked_sha256` and the object isn't an interrupted re-mask, then the system shall flag the unit.
- [ ] **DETECT-FLOW-008**: If a fetched FLAC is an interrupted re-mask (its `mask-version` is one above the recorded version), then the system shall mark the unit `failed` (`remask_in_progress`).
- [ ] **DETECT-FLOW-009**: If a file fails to decode or the model raises an error, then the system shall mark the unit `failed`.

## Re-runs and New Models

- [ ] **DETECT-RERUN-001**: When a detect run uses a `model_id` not seen before, the system shall write its detections to that `model_id`'s partition without modifying other partitions.
- [ ] **DETECT-RERUN-002**: When a file is re-masked, the system shall not make its detect units pending.
