# Validate Specs

EARS requirements for [validate](validate-design.md). Markers: `[x]` implemented, `[ ]` active
gap, `[D]` deferred. Validate is interfaces only in Phase 1, so its specs are deferred until the
first detections exist.

## Sampling

- [D] **VALID-SAMPLE-001**: When the sampler draws a review batch, the system shall stratify `detections_current` for one `model_id` by taxon and confidence bin (`[0.10, 0.25)`, `[0.25, 0.50)`, `[0.50, 0.75)`, `[0.75, 1.00]`) with a recorded seed.
- [D] **VALID-SAMPLE-002**: When the sampler draws a review batch, the system shall include only target taxa that are in the geo list or meet a minimum detection count, and never a detection with `overlaps_current_mask` true.

## Clips

- [D] **VALID-CLIP-001**: When the system cuts a review clip, it shall cut the detection's 3 s window plus 1.5 s of context on each side from the current archived masked audio, store it at `clips/<detection_id>.flac`, and write a `clips` row.
- [D] **VALID-CLIP-002**: If the archived object for a clip is an interrupted re-mask, then the system shall mark the clip-cutting unit `failed` (`remask_in_progress`).
- [D] **VALID-CLIP-003**: The review flow shall read only masked clips from the store and never read the quarantine.

## Calibration

- [D] **VALID-CAL-001**: When the system fits a calibration, it shall fit a logistic regression of correct on logit(confidence) per taxon and `model_id`, excluding `unsure` labels, and set `threshold_at_target` to the confidence where fitted P(correct) equals the target precision, or null.
- [D] **VALID-CAL-002**: If a taxon has fewer than `min_labels` (default 50) labels or fewer than 10 `correct` labels, then the system shall leave it `unvalidated`.
