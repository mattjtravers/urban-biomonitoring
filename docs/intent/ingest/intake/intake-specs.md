# Intake Specs

EARS requirements for [intake](intake-design.md). Markers: `[x]` implemented, `[ ]` active gap,
`[D]` deferred.

## Recorder Adapters

- [ ] **INGEST-INTAKE-ADAPT-001**: The system shall call recorder adapters only through the `RecorderAdapter` protocol (ingest § Recorder Adapter Interface) and select the adapter named by the deployment's `adapter` field, never by inspecting the card.
- [ ] **INGEST-INTAKE-ADAPT-002**: The system shall give recorder adapters no access to the store, the ledger, the deployment, or files outside the part being ingested, and shall perform all clock correction, UTC conversion, deployment checks, and anomaly recording in intake, not in adapters.
- [ ] **INGEST-INTAKE-ADAPT-003**: The system shall use an adapter's `device_position` only for the site position check, and never persist it outside the private archive sidecar.
- [ ] **INGEST-INTAKE-ADAPT-004**: When intake constructs a recorder adapter, the system shall pass it its own `[ingest.<key>]` table from `config/pipeline.toml`.

## Quarantine

- [ ] **INGEST-INTAKE-Q-001**: The system shall create the quarantine directory with mode `0700`.
- [ ] **INGEST-INTAKE-Q-002**: If an intake path argument, resolved with symlinks followed, doesn't lie under the resolved `paths.quarantine`, then the system shall refuse it with a `ConfigError`.
- [ ] **INGEST-INTAKE-Q-003**: The system shall never write unmasked audio outside `paths.quarantine`.

## Ingest Flow

- [ ] **INGEST-INTAKE-FLOW-001**: When `urbanbio ingest PATH` runs, the system shall compute the SHA-256 of every file under `PATH`, streamed in 1 MiB blocks, before any parser opens a file.
- [ ] **INGEST-INTAKE-FLOW-002**: When `urbanbio ingest` runs without `--retrieval`, the system shall allocate the next `retrieval_id` for the deployment from the retrieval prefixes already in the store and register the files as part `01`.
- [ ] **INGEST-INTAKE-FLOW-003**: When `urbanbio ingest` runs with `--retrieval R`, the system shall register the files as the next part number of retrieval `R`.
- [ ] **INGEST-INTAKE-FLOW-004**: When a part is registered, the system shall write `manifest-part<NN>.json`, holding the part's `<label>`, every file's path, size, and SHA-256, and each device file's store key, to the quarantine and to the store, and only then move `PATH` to `<retrieval_id>/card/part<NN>/` by rename.
- [ ] **INGEST-INTAKE-FLOW-005**: When a file's path and SHA-256 already appear in an earlier part's manifest of the same retrieval, the system shall record it as a duplicate and skip it.
- [ ] **INGEST-INTAKE-FLOW-006**: When the adapter's `list_media` classifies a file as unknown, the system shall record an `unknown_file` anomaly and leave the file in place.
- [ ] **INGEST-INTAKE-FLOW-007**: When the file-status lookup shows an audio file's `audio_file_id` already archived, the system shall skip the file as a duplicate.
- [ ] **INGEST-INTAKE-FLOW-008**: When ingest processes an audio file, the system shall read its metadata through the adapter and compute `original_pcm_sha256` over the decoded samples.
- [ ] **INGEST-INTAKE-FLOW-009**: If an audio file's declared RIFF or `data` chunk size exceeds the bytes present, then the system shall flag the file `truncated`.
- [ ] **INGEST-INTAKE-FLOW-010**: When a `truncated` waiver covers an audio file, the system shall ingest the frames present.
- [ ] **INGEST-INTAKE-FLOW-011**: If an audio file's sample rate, device serial, or recorder name differs from the deployment's, or its bit depth isn't 16 or its channel count isn't 1, then the system shall flag the file `unexpected_format`.
- [ ] **INGEST-INTAKE-FLOW-012**: If an audio file's `start_utc` is earlier than the deployment's `start_utc` minus 10 minutes, or its end is later than the deployment's `end_utc` (when set) or `--retrieved` plus 10 minutes, then the system shall flag the file `outside_deployment`.
- [ ] **INGEST-INTAKE-FLOW-013**: When ingest parses the device log, the system shall convert each row's device-local time with the UTC offset of the audio files from the same power-on session, falling back to the deployment's `schedule.utc_offset_minutes`.
- [ ] **INGEST-INTAKE-FLOW-014**: When ingest finishes a batch of files, the system shall write the batch's `audio_files` rows and ingest ledger rows (RUN-ORDER-002), and when the run ends it shall write the part's `media_retrievals` row computed over every file in the part.
- [ ] **INGEST-INTAKE-FLOW-015**: When an ingest run ends, the system shall write one new `deployments` row when the deployment's `firmware_version` is null and a file of the part reports one (taking the first such file by `start_utc`), or when the retrieval's `clock_drift` anomaly sets `timestamp_issues`, combining both changes in that row.

## Resuming

- [ ] **INGEST-INTAKE-RESUME-001**: When `urbanbio ingest PATH` runs and `PATH` exists, the system shall resume the registered part of the same deployment whose `card/part<NN>/` directory doesn't exist and whose manifest equals the one just computed, continuing at the move, and shall register a new part when none matches.
- [ ] **INGEST-INTAKE-RESUME-002**: When `urbanbio ingest PATH` runs and `PATH` doesn't exist, the system shall take `PATH`'s last path component as the label and resume the registered part of the same deployment that has been moved into the quarantine, whose manifest records that label, and that isn't yet safe to wipe.
- [ ] **INGEST-INTAKE-RESUME-003**: If resuming finds no matching part when `PATH` doesn't exist, or more than one matching part in either case, then the system shall raise a `ConfigError` naming the candidates.

## Time Normalization

- [ ] **INGEST-INTAKE-TIME-001**: The system shall normalize each audio file's start to UTC with the adapter's per-file UTC offset, or the deployment's `schedule.utc_offset_minutes` when the adapter reports none.
- [ ] **INGEST-INTAKE-TIME-002**: If neither the adapter nor the deployment provides a UTC offset for an audio file, then the system shall flag the file `timezone_unknown`.
- [ ] **INGEST-INTAKE-TIME-003**: The system shall never apply a named time zone's daylight-saving rules to device time.
- [ ] **INGEST-INTAKE-TIME-004**: When audio files in one retrieval report different UTC offsets, the system shall normalize each with its own offset and record an `offset_change` anomaly where the offset changes.
- [ ] **INGEST-INTAKE-TIME-005**: Where `--clock-offset-s X` is given and `|X| ≥ 0.5`, the system shall set each file's `clock_correction_s` to `-X × (t − t0) / (t1 − t0)`, with `t0` the previous retrieval's `retrieved_utc` or the deployment start, `t1` this retrieval's `retrieved_utc`, and `t` the file's start, and record `X` as the retrieval's `clock_offset_s`.
- [ ] **INGEST-INTAKE-TIME-006**: Where `--clock-offset-s X` is given and `|X| > 60`, the system shall record a `clock_drift` anomaly and set the deployment's `timestamp_issues` through INGEST-INTAKE-FLOW-015.
- [ ] **INGEST-INTAKE-TIME-007**: Where `--clock-offset-s` is not given, the system shall apply no clock correction and leave `clock_offset_s` null.

## Same-Name Conflicts

- [ ] **INGEST-INTAKE-NAME-001**: When two audio files in one retrieval share an `original_filename` with different SHA-256 and exactly one passes all checks, the system shall keep that file and flag the other `superseded`, with `related_unit_id` naming the file that supersedes it.
- [ ] **INGEST-INTAKE-NAME-002**: When two audio files in one retrieval share an `original_filename` with different SHA-256 and both pass or both fail their checks, the system shall flag both `filename_conflict`.

## Retrieval Health

- [ ] **INGEST-INTAKE-HEALTH-001**: When consecutive file starts within a configured daily window are further apart than `duty_on_s + duty_off_s + 5 s` (or `max_file_length_s + 5 s` for a continuous schedule), the system shall record a `gap` anomaly.
- [ ] **INGEST-INTAKE-HEALTH-002**: When the device log has more than one power-on session, or the adapter lists diagnostics files in `DeviceLog.diagnostics`, the system shall record a `reboot` anomaly for each later session and each diagnostics file, and set `reboot_count` to the number of sessions after the first.
- [ ] **INGEST-INTAKE-HEALTH-003**: When the number of files the device log reports completed differs from the audio files present, the system shall record a `file_count_mismatch` anomaly.
- [ ] **INGEST-INTAKE-HEALTH-004**: When `DeviceLog.low_battery_v` is set and any logged voltage is below it, the system shall record a `low_battery` anomaly.
- [ ] **INGEST-INTAKE-HEALTH-005**: The system shall record retrieval anomalies without flagging any unit.
- [ ] **INGEST-INTAKE-HEALTH-006**: When `DeviceLog.unparsed_rows` is non-empty, the system shall record a `summary_unparseable` anomaly naming each row, without flagging any audio file.

## Site Position Check

- [ ] **INGEST-INTAKE-POS-001**: When an audio file's header has a non-zero device position, the system shall compute its distance from the site through `config.site_offset_m` and record a `position_mismatch` anomaly when it exceeds 500 m.

## Disk Budget

- [ ] **INGEST-INTAKE-DISK-001**: If the quarantine and work directories together exceed `quarantine.budget_gb`, then `ingest` shall refuse to start with a `ConfigError` (`disk_budget_exceeded`).
- [ ] **INGEST-INTAKE-DISK-002**: If free space on the quarantine filesystem is below `quarantine.min_free_gb`, then `ingest` and `archive` shall refuse to start with a `ConfigError` (`disk_free_low`).
- [ ] **INGEST-INTAKE-DISK-003**: When `ingest` or `archive` refuses to start for disk space, the system shall state the current usage, the budget, and the free space.

## Purge

- [ ] **INGEST-INTAKE-PURGE-001**: Except as INGEST-INTAKE-PURGE-004 and INGEST-INTAKE-PURGE-005 allow, the system shall delete a quarantine original only when the archive ledger in the store shows its `<audio_file_id>:v1` unit `succeeded`, the file isn't held for speech labeling, no stage's latest ledger row for the file is `flagged`, and none of its current `ingest`, `screen`, or `archive` successes came from a run with `git_dirty: true`.
- [ ] **INGEST-INTAKE-PURGE-002**: When the archive stage has written a batch's ledger part, the system shall purge that batch's eligible originals, deleting each before writing its `purge` ledger row.
- [ ] **INGEST-INTAKE-PURGE-003**: When a purge unit's original is already gone and its archive success is in the store, the system shall record the unit `succeeded`.
- [ ] **INGEST-INTAKE-PURGE-004**: When `urbanbio purge --retrieval R` runs, the system shall delete every eligible original of the retrieval, every original flagged `superseded` whose superseding file is archived, and every device log, diagnostics file, and unknown file already in the store.
- [ ] **INGEST-INTAKE-PURGE-005**: When `urbanbio purge --retrieval R --discard UNIT --reason TEXT` runs, the system shall delete each named flagged audio file without writing it anywhere, recording a `succeeded` purge unit with `reason_code` `discarded` and the reason in the purge run.
- [ ] **INGEST-INTAKE-PURGE-006**: When `urbanbio purge --retrieval R --discard PATH --reason TEXT` names a device file recorded in an `unexpected_audio` anomaly by its manifest path, the system shall delete it without writing it anywhere and write a `device_file_discards` row (DATA-RET-005).
- [ ] **INGEST-INTAKE-PURGE-007**: When only the part manifests remain in a retrieval's quarantine directory, the system shall delete that directory.

## Card Wipe Readiness

- [ ] **INGEST-INTAKE-WIPE-001**: When `urbanbio retrieval status R` runs, the system shall print **safe to wipe** only when every audio file in the retrieval's part manifests is archived and verified, superseded by an archived copy, or discarded; every device log, diagnostics file, and unknown file is in the store or, for an `unexpected_audio` file, has a `device_file_discards` row; no audio file's current `archive` success came from a run with `git_dirty: true`; and the store isn't on the same filesystem as `paths.quarantine`.
- [ ] **INGEST-INTAKE-WIPE-002**: When `urbanbio retrieval status R` doesn't print safe to wipe, the system shall list the files and conditions that prevent it.
- [ ] **INGEST-INTAKE-WIPE-003**: When `urbanbio retrieval status R` runs, the system shall report per-file status, files archived but not yet detected, work-directory FLACs still on disk, and speech-labeling holds older than 30 days.

## Speech-Labeling Hold

- [ ] **INGEST-INTAKE-HOLD-001**: While an audio file is held for speech labeling, the system shall not archive or purge it, and its retrieval shall not be safe to wipe.
- [ ] **INGEST-INTAKE-HOLD-002**: When `urbanbio speech label --import` releases a hold, the system shall remove the file's hard link from `quarantine/speech-labeling/`.
- [ ] **INGEST-INTAKE-HOLD-003**: When `urbanbio archive --retrieval R` runs after a hold is released, the system shall archive the released file with its `manual` speech events and purge it.

## Deferred

- [D] **INGEST-INTAKE-TIME-008**: Where a recurring noise event at a known time of day is available, the system shall estimate clock drift from it as a cross-check on the operator's `--clock-offset-s`.
