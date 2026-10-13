# Archive Specs

EARS requirements for [archive](archive-design.md). Markers: `[x]` implemented, `[ ]` active
gap, `[D]` deferred.

## Archive Flow

- [ ] **ARCH-FLOW-001**: When `urbanbio archive --retrieval R` runs, the system shall process each screened audio file of the retrieval that no stage's latest ledger row flags and that isn't held for speech labeling, and shall write no ledger row for a held file.
- [ ] **ARCH-FLOW-002**: When the archive stage processes a file, the system shall first confirm that the quarantine original's SHA-256 still equals `original_sha256`.
- [ ] **ARCH-FLOW-003**: When the archive stage processes a file for its first archive, the system shall build its `MaskedAudio` with `apply_mask` from the file's current speech events, including `manual` events, as mask version 1.
- [ ] **ARCH-FLOW-004**: When the archive stage encodes a `MaskedAudio`, the system shall write it to the work directory with `soundfile.write(..., format="FLAC", subtype="PCM_16", compression_level=archive.flac_compression_level)` and no descriptive tags.
- [ ] **ARCH-FLOW-005**: When the archive stage has encoded a FLAC, the system shall decode it and require exact array equality with the masked samples, equality with the original wherever the mask is false, and zeros wherever it is true, and shall compute `masked_sha256` over the FLAC bytes, `masked_pcm_sha256` over the decoded samples, and `masked_bytes` as the FLAC's size.
- [ ] **ARCH-FLOW-006**: Before writing an archive object, the system shall `head` its key, and when an object with the same `mask-version` and the same `masked-pcm-sha256` exists, shall skip the write and record the unit with the existing object's checksum.
- [ ] **ARCH-FLOW-007**: If an archive object with the same `mask-version` and a different `masked-pcm-sha256` exists at the key, then the system shall flag the unit `archive_conflict` without writing.
- [ ] **ARCH-FLOW-008**: When the archive stage writes a file, the system shall `put` the FLAC and then the sidecar with `overwrite=False`, the precomputed SHA-256, and metadata `audio-file-id`, `mask-version`, `masked-sha256`, `masked-pcm-sha256`, `original-sha256`, and `run-id`.
- [ ] **ARCH-FLOW-009**: When an archive unit's objects are written, the system shall record an AudioMaskVersion row and a `succeeded` `archive` ledger row with unit ID `<audio_file_id>:v<mask_version>`, written in the batch's part files (RUN-ORDER-002).
- [ ] **ARCH-FLOW-010**: When a batch's ledger part file is written and the run doesn't have `--no-purge`, the system shall purge the batch's eligible originals through intake's purge (INGEST-INTAKE-PURGE-001).
- [ ] **ARCH-FLOW-011**: If any archive check fails, then the system shall raise `IntegrityError`, flag the unit, and leave the original in the quarantine.
- [ ] **ARCH-FLOW-012**: The system shall run archive steps from re-verification to the store write in at most `resources.workers` worker processes, one file per worker at a time, and write batches and purges only from the main process.
- [ ] **ARCH-FLOW-013**: The system shall keep each work-directory FLAC until the detect stage has processed its file.
- [ ] **ARCH-FLOW-014**: The archive write API shall accept only `MaskedAudio`.

## Device Files

- [ ] **ARCH-DEV-001**: When the archive stage runs for a retrieval, the system shall write each device log, diagnostics file, and unknown file in the part manifests verbatim to its device-file key (STORE-KEY-004).
- [ ] **ARCH-DEV-002**: If a device file's header identifies it as audio (RIFF/WAVE, FLAC, Ogg, or MP3), then the system shall not write it to the store and shall record an `unexpected_audio` row in `retrieval_anomalies` (DATA-RET-004) naming its manifest path.
- [ ] **ARCH-DEV-003**: Before writing a device file, the system shall `head` its key, skipping the write when an object with the same SHA-256 exists, and recording `IntegrityError` (`archive_conflict`) in the run's errors and leaving the file in the quarantine when the existing object's SHA-256 differs.

## Header Sidecar

- [ ] **ARCH-SIDE-001**: When the archive stage builds a header sidecar, the system shall write `<audio_file_id>.header.json` as UTF-8 JSON with `schema` (`urbanbio.header-sidecar/1`), `audio_file_id`, `original_filename`, `original_sha256`, `original_bytes`, `container`, `chunks`, `guano`, `adapter`, and `adapter_version`.
- [ ] **ARCH-SIDE-002**: The system shall store every non-`data` chunk in the sidecar byte for byte as base64, with its ID, offset, and size, and list the `data` chunk by ID, offset, and size with `data_b64` null.
- [ ] **ARCH-SIDE-003**: When a WAV container is rebuilt from a sidecar and its archived FLAC, the system shall reproduce the original file exactly except for masked samples, which are zero.

## Re-masking

- [ ] **ARCH-REMASK-001**: When `rescreen` hands the archive a `MaskedAudio` at `mask_version + 1`, the system shall encode, verify, and write it to the same key with `overwrite=True`, leave the sidecar unchanged, and purge nothing.
- [ ] **ARCH-REMASK-002**: When a re-mask is written, the system shall record the new AudioMaskVersion row and the new `speech_events` rows under the `rescreen` unit (`<audio_file_id>:v<mask_version>` of the version re-screened), cited in that unit's `output_refs`, and write no `archive` ledger row.
- [ ] **ARCH-REMASK-003**: When a re-mask adds masked intervals, the system shall delete from `clips/` every clip of the file whose window, including context, overlaps an added interval and that isn't already withdrawn, and write a `clip_withdrawals` row for each, before the unit's ledger row.
- [ ] **ARCH-REMASK-004**: When re-mask clip withdrawal finds a clip object already gone, the system shall still write its `clip_withdrawals` row.
- [ ] **ARCH-REMASK-005**: When a resuming `rescreen` adopts an interrupted re-mask, the system shall copy the crashed run's uncited rows with the same `stage` and `unit_id` (AudioMaskVersion, `speech_events`, `clip_withdrawals`) into its own batch under its own `run_id` with their content IDs unchanged, finish pending clip withdrawals, and write the ledger row.
- [ ] **ARCH-REMASK-006**: When a reader fetches an archive object that doesn't match the current `masked_sha256`, whose `mask-version` metadata is one above the recorded version and whose `run-id` names an earlier `rescreen` run (an interrupted re-mask), the system shall treat that reader's unit as `failed` (`remask_in_progress`), except that `rescreen` itself adopts the re-mask (SCREEN-RESCREEN-002).
- [ ] **ARCH-REMASK-007**: If an archive object disagrees with its records in any way other than an interrupted re-mask, then the system shall flag the reading unit `archive_conflict`.
- [ ] **ARCH-REMASK-008**: The `audio_mask_versions_current` view shall count rows cited by a `succeeded` `archive` unit (first versions) or `rescreen` unit (later versions).

## Deferred

- [D] **ARCH-TIER-001**: Where the archive exceeds about 5 TB, the system shall support moving archived audio older than two years to S3 Glacier Deep Archive.
