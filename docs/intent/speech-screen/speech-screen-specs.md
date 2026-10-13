# Speech Screen Specs

EARS requirements for [speech-screen](speech-screen-design.md). Markers: `[x]` implemented,
`[ ]` active gap, `[D]` deferred.

## Detectors

- [ ] **SCREEN-DET-001**: When the `screen` or `rescreen` stage screens an audio file, the system shall run both the `birdnet-human` and `silero-vad` detectors on it and keep every event either detector returns.
- [ ] **SCREEN-DET-002**: If either speech detector raises an error or returns no result for a file, then the system shall record the screen unit `failed` (`screen_detector_failed`) and write no SpeechEvents for that file.
- [ ] **SCREEN-DET-003**: When the `birdnet-human` detector screens a file, the system shall run BirdNET acoustic V2.4 through `birdnet==1.1.1` on LiteRT with `predict_arrays` on the original 48 kHz samples, with `custom_species_list = screen.birdnet_mask_labels`, `default_confidence_threshold = screen.birdnet_stats_floor`, `overlap_duration_s = screen.birdnet_overlap_s`, `top_k = None`, sigmoid sensitivity 1.0, bandpass 0–15,000 Hz, and no location filter.
- [ ] **SCREEN-DET-004**: When a `birdnet-human` window scores at or above `screen.birdnet_threshold`, the system shall record a SpeechEvent `[t, t + 3.0)` with that score.
- [ ] **SCREEN-DET-005**: When the `silero-vad` detector screens a file, the system shall resample the samples from 48 kHz to 16 kHz with `scipy.signal.resample_poly(x, 1, 3)` as float32 in [-1, 1] and run `silero-vad==6.2.3` with `load_silero_vad(onnx=True)`, `threshold = screen.silero_threshold`, `min_speech_duration_ms = screen.silero_min_speech_ms`, `min_silence_duration_ms = 100`, `speech_pad_ms = 30`, and `window_size_samples = 512`.
- [ ] **SCREEN-DET-006**: When `silero-vad` returns a speech segment, the system shall record a SpeechEvent with its start and end in seconds and the maximum frame probability within it as the score.
- [ ] **SCREEN-DET-007**: The system shall not mask BirdNET's `Human non-vocal` or `Human whistle` classes unless they are listed in `screen.birdnet_mask_labels`.
- [ ] **SCREEN-SCOPE-001**: When `urbanbio screen --retrieval R` runs, the system shall screen only the retrieval's audio files whose current `ingest` unit succeeded and that have no succeeded `archive` unit, leaving archived files to `rescreen`.

## Padding, Merging, and the Mask

- [ ] **SCREEN-MASK-001**: When the system builds a mask for a file of duration `D`, it shall pad each event `[s, e)` to `[max(0, s − screen.padding_s), min(D, e + screen.padding_s))`, store the padded bounds as `masked_start_s` and `masked_end_s`, and take the mask as the union of the padded intervals, merging overlapping or touching intervals.
- [ ] **SCREEN-MASK-002**: When the system applies a mask, it shall zero sample indices `[floor(masked_start_s × sr), ceil(masked_end_s × sr))` on every channel, keeping the array's length, sample rate, and dtype.
- [ ] **SCREEN-MASK-003**: The system shall construct `MaskedAudio` only through `mask.apply_mask(original: QuarantinedAudio, events)` (mask version 1) and `mask.remask(current: MaskedAudio, events)` (mask version + 1).
- [ ] **SCREEN-MASK-004**: If `QuarantinedAudio` is loaded from a path that isn't under the resolved quarantine root, then the system shall raise `PrivacyError`.
- [ ] **SCREEN-MASK-005**: The system shall give `MaskedAudio` the `audio_file_id`, `mask_version`, the speech event IDs applied (cumulative across versions), and `masked_seconds`.
- [ ] **SCREEN-MASK-006**: When a screened file has no speech events, the system shall still build its `MaskedAudio` through `apply_mask` with zero masked seconds.
- [ ] **SCREEN-MASK-007**: When a file's mask covers its whole duration, the system shall still produce its `MaskedAudio` as all zeros for archiving.
- [ ] **SCREEN-MASK-008**: When `remask` receives a `MaskedAudio` decoded from the archive, the system shall first verify the decoded samples against the current `masked_pcm_sha256`.

## Screen Outputs

- [ ] **SCREEN-OUT-001**: When a file is screened, the system shall write one `screen_file_stats` row with `audio_file_id`, `run_id`, `birdnet_max_score`, `silero_max_prob`, `event_count`, and `masked_seconds`.
- [ ] **SCREEN-OUT-002**: The system shall write SpeechEvents to the `speech_events` table with offsets, detector, label, and score only, with no audio or derived features.
- [ ] **SCREEN-OUT-003**: When a screen run ends, the system shall report per-retrieval totals of events per detector, files with any event, masked seconds, and masked fraction of recorded time.
- [ ] **SCREEN-OUT-004**: When more than `screen.max_masked_fraction` of a retrieval's recorded time is masked, the system shall record a `high_masked_fraction` row in `retrieval_anomalies` (DATA-RET-004) and still mask every event.

## Speech Sampling

- [ ] **SCREEN-SAMPLE-001**: When `urbanbio speech sample --retrieval R --n N` runs, the system shall choose `N` screened, not-yet-archived files of the retrieval whose current `ingest` unit succeeded, in equal thirds from: files where a detector fired; near misses (no event, but `birdnet_max_score` ≥ 0.02 or `silero_max_prob` ≥ 0.15); and the rest at random.
- [ ] **SCREEN-SAMPLE-002**: When `urbanbio speech sample` selects files, the system shall use a seeded RNG and record the seed in the run.
- [ ] **SCREEN-SAMPLE-003**: When `urbanbio speech sample` selects a file, the system shall hard-link the original into `quarantine/speech-labeling/<audio_file_id>.wav` and record the hold link in the unit's `output_refs`.
- [ ] **SCREEN-SAMPLE-004**: When `urbanbio process --speech-sample N` runs, the system shall run speech sampling after `screen` and before `archive`.

## Label Import

- [ ] **SCREEN-LABEL-001**: When `urbanbio speech label --import` runs, the system shall read `quarantine/speech-labeling/labels.csv` with columns `audio_file_id`, `start_s`, `end_s`, `reviewer_id`, treating a row with empty `start_s` and `end_s` as "no speech".
- [ ] **SCREEN-LABEL-002**: If `labels.csv` lacks a row for any held file, or has a malformed row, then `urbanbio speech label --import` shall refuse the whole import, writing nothing and releasing no hold.
- [ ] **SCREEN-LABEL-003**: When `urbanbio speech label --import` imports a held file's labels, the system shall write its `speech_labels` rows (no audio), write a `manual` SpeechEvent with score 1.0 for each labeled interval not wholly inside the file's detector mask, and release the hold.

## Speech Recall

- [ ] **SCREEN-RECALL-001**: When `urbanbio speech recall` runs, the system shall compute event recall as labeled speech intervals with at least 90% of their duration inside the detector mask (the padded union of the file's `birdnet-human` and `silero-vad` events only) divided by all labeled intervals, with a Wilson 95% interval.
- [ ] **SCREEN-RECALL-002**: When `urbanbio speech recall` runs, the system shall compute time recall as labeled speech seconds inside the detector mask divided by labeled speech seconds.
- [ ] **SCREEN-RECALL-003**: When `urbanbio speech recall` runs, the system shall compute masked fraction as masked seconds over recorded seconds using the archived mask, including `manual` events.
- [ ] **SCREEN-RECALL-004**: When `urbanbio speech recall` runs, the system shall report labeled intervals caught by `birdnet-human` only, `silero-vad` only, and both, excluding `manual` events.
- [ ] **SCREEN-RECALL-005**: When `urbanbio speech recall` runs, the system shall report each metric per stratum and weighted to the population by stratum sizes.

## Re-screening

- [ ] **SCREEN-RESCREEN-001**: When `urbanbio rescreen --retrieval R` processes a file, the system shall download its current FLAC and verify it against the current `masked_sha256`.
- [ ] **SCREEN-RESCREEN-002**: If the downloaded FLAC doesn't match the current `masked_sha256`, its object metadata has a `mask-version` one above the recorded version, and its `run-id` names an earlier `rescreen` run, then the system shall adopt that run's outputs for the unit and finish it without re-screening.
- [ ] **SCREEN-RESCREEN-003**: If the downloaded FLAC doesn't match the current `masked_sha256` in any other way, then the system shall flag the rescreen unit `archive_conflict`.
- [ ] **SCREEN-RESCREEN-004**: When `rescreen` re-runs the detectors on a file, the system shall use the current parameters and add the file's `manual` events.
- [ ] **SCREEN-RESCREEN-005**: When the new events' padded union zeroes samples that the current mask doesn't, the system shall build `remask(current, events)` and hand it to the archive as `mask_version + 1`; otherwise it shall record the unit `succeeded` with no new mask version.

## Deferred

- [D] **SCREEN-DET-008**: Where `Human whistle` is added to `screen.birdnet_mask_labels`, the system shall mask it like `Human vocal`.
- [D] **SCREEN-REUSE-001**: Where BirdNET outputs from screening are reused for detect, the system shall reuse them only for windows the mask leaves untouched.
