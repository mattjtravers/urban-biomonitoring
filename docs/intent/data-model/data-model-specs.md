# Data Model Specs

EARS requirements for [data-model](data-model-design.md). Markers: `[x]` implemented, `[ ]`
active gap, `[D]` deferred.

## Models

- [ ] **DATA-MODEL-001**: The system shall define every entity in data-model § Entities as a Pydantic v2 model in `urbanbio.models` with exactly the listed fields, types, and nullability, and with `model_config = ConfigDict(frozen=True, extra="forbid")`.
- [ ] **DATA-MODEL-002**: The system shall require every datetime field whose name ends in `_utc` to be timezone-aware UTC, and every field whose name ends in `_local` to be naive, rejecting other values at validation.
- [ ] **DATA-MODEL-003**: The system shall not define, in any persisted model or Arrow schema, a field for exact coordinates, addresses, or people's names, including any key listed in `PRIVATE_KEYS`.
- [ ] **DATA-MODEL-004**: The system shall pass data between stages only as these models or as Arrow tables with their declared schemas, never as untyped dicts.

## Identifiers

- [ ] **DATA-ID-001**: The system shall require every ID field to match `^[a-z0-9-]+$`, `site_id` to match `^site[0-9]+$`, `deployment_id` to be `<site_id>-dep<NNN>`, and `retrieval_id` to be `<deployment_id>-r<NNN>`.
- [ ] **DATA-ID-002**: When a deployment or retrieval is created, the system shall assign the next sequence number after the highest existing one for its site (deployments) or deployment (retrievals).
- [ ] **DATA-ID-003**: The system shall derive `audio_file_id` as `af-` followed by the first 16 hex characters of the original file's SHA-256.
- [ ] **DATA-ID-004**: The system shall derive `speech_event_id` (`se-`), `detection_id` (`det-`), `label_id` (`lbl-`), and `calibration_id` (`cal-`) as the prefix followed by the first 16 hex characters of the SHA-256 of the canonical JSON (RUN-KEY-003) of an object holding, under their own names, respectively: `audio_file_id`, `run_id`, `detector`, `start_s`; `model_id`, `audio_file_id`, `start_s`, `label`; `detection_id`, `reviewer_id`, `labeled_utc`; `taxon_key`, `model_id`, `label_set_hash`, with datetimes as ISO 8601 UTC strings.
- [ ] **DATA-ID-005**: The system shall exclude `mask_version` and `run_id` from the `detection_id` hash, so the same model, file, window start, and label give the same `detection_id` in every run and at every mask version.
- [ ] **DATA-ID-006**: The system shall require `reviewer_id` to be an opaque handle matching the ID pattern, never a name.

## Site

- [ ] **DATA-SITE-001**: When configuration loads, the system shall build each `Site` with generalized coordinates rounded to the site's `coordinate_precision_deg` when set, otherwise `publish.coordinate_precision_deg`, with `coordinate_uncertainty_m` and `generalization` describing the precision used.
- [ ] **DATA-SITE-002**: The system shall require each Site's `timezone` to name a zone in the IANA database, checked with `zoneinfo`.

## Deployment

- [ ] **DATA-DEP-001**: The system shall store `Deployment` rows as append-only versions, writing a new row whenever `firmware_version` is filled, `timestamp_issues` is set, or `end_utc` is set, and the `current_deployments` view shall take the latest counted row per `deployment_id`, counting `admin`-written rows under RUN-CURRENT-001 and `ingest`-written rows (`stage` `ingest`, null `unit_id`) under RUN-CURRENT-004.
- [ ] **DATA-DEP-002**: If `urbanbio deployment create` would create a deployment whose interval overlaps an active deployment on the same `device_serial`, then the system shall refuse with a `ConfigError`.
- [ ] **DATA-DEP-003**: The system shall define `RecordingSchedule` with `duty_on_s`, `duty_off_s` (0 for continuous), `max_file_length_s`, `daily_windows` (local recorder time; null for around the clock), and `utc_offset_minutes`.
- [ ] **DATA-DEP-004**: When a deployment is exported in Camtrap DP form, the system shall map its fields to the Camtrap DP 1.0.2 `deployments` fields listed in data-model § Deployment, take `latitude` and `longitude` from the Site's generalized coordinates, and leave `setupBy` empty.

## MediaRetrieval

- [ ] **DATA-RET-001**: The system shall write one `MediaRetrieval` row per ingest part, with `part` numbered 1, 2, … within the retrieval and `manifest_sha256` the SHA-256 of that part's manifest.
- [ ] **DATA-RET-002**: The `current_retrievals` view shall take the latest row per (`retrieval_id`, `part`), then combine parts per `retrieval_id`: recomputing `file_count` and `total_bytes` from current `audio_files` rows, taking the minimum or maximum across parts for time spans and battery values, and concatenating the parts' anomalies with the retrieval's counted `retrieval_anomalies` rows.
- [ ] **DATA-RET-003**: The system shall restrict each `Anomaly` to a `code`, optional `start_utc` and `end_utc`, and a `detail`, with `code` one of `gap`, `reboot`, `file_count_mismatch`, `low_battery`, `clock_drift`, `offset_change`, `position_mismatch`, `unknown_file`, `unexpected_audio`, `summary_unparseable`, or `high_masked_fraction`.
- [ ] **DATA-RET-004**: The system shall record anomalies found after ingest (`high_masked_fraction` by the speech screen, `unexpected_audio` by the archive) as `retrieval_anomalies` rows with `retrieval_id`, `part` (null for the whole retrieval), the `Anomaly` fields, and `run_id`, never by rewriting a `media_retrievals` row.
- [ ] **DATA-RET-005**: The system shall record each device file the operator discards as a `device_file_discards` row with `retrieval_id`, `part`, `manifest_path`, `reason`, and `run_id`.

## AudioFile

- [ ] **DATA-AF-001**: The system shall map `AudioFile` to Camtrap DP `media` as `audio_file_id` → `mediaID`, `deployment_id` → `deploymentID`, `start_utc` → `timestamp`, `archive_key` → `filePath`, `filePublic = false`, and `fileMediatype = audio/flac`.
- [ ] **DATA-AF-002**: The `audio_file_status` view shall report, per `audio_file_id`, the first of: `discarded` (a succeeded `purge` unit with `reason_code` `discarded`); `flagged` (some stage's latest ledger row is `flagged`); `held` (a succeeded `speech-sample` unit whose `output_refs` include the hold link and no later succeeded `speech-label` unit); otherwise the furthest succeeded of `purged`, `archived`, `screened`, or `quarantined`.
- [ ] **DATA-AF-003**: Where the `s3` backend is selected, the `audio_file_tier` view shall report `STANDARD` for a file until `store.glacier_transition_days` after its current mask version's `created_utc`, and `GLACIER_IR` from then on, except that a file whose current mask version's `masked_bytes` is at most 131,072 stays `STANDARD`.
- [ ] **DATA-AF-004**: Where the `filesystem` backend is selected, the `audio_file_tier` view shall report `FILESYSTEM` for every file.

## AudioMaskVersion

- [ ] **DATA-MASK-001**: The system shall write a new `AudioMaskVersion` row with the next `mask_version` for each re-mask, starting at 1 for the first archive, and never update an existing row.
- [ ] **DATA-MASK-002**: The system shall set `AudioMaskVersion.storage_class` to `STANDARD` on the `s3` backend and `FILESYSTEM` on the `filesystem` backend.
- [ ] **DATA-MASK-003**: The `current_masks` view shall take, per `audio_file_id`, the current `audio_mask_versions` row with the highest `mask_version`.

## SpeechEvent

- [ ] **DATA-SPEECH-001**: The system shall restrict `SpeechEvent.detector` to `birdnet-human`, `silero-vad`, or `manual`, and store no audio content in a speech event.

## Detection

- [ ] **DATA-DET-001**: The system shall set `Detection.start_utc` to the file's `start_utc` plus the detection's `start_s`.
- [ ] **DATA-DET-002**: The system shall store `overlaps_mask_at_detect` as whether the detection window intersected a masked interval of the mask version analyzed, and never change it afterwards.
- [ ] **DATA-DET-003**: The `detections_current` view shall hold, for each (`audio_file_id`, `model_id`), the rows of the latest succeeded `detect` unit on that file whatever mask version it analyzed, with an added `overlaps_current_mask` column computed against the file's `current_masks` row.

## Clip

- [ ] **DATA-CLIP-001**: The system shall write a `clips` row for every clip object written, including a clip cut again after a withdrawal.
- [ ] **DATA-CLIP-002**: The system shall restrict `ClipWithdrawal` to `detection_id`, `site_id`, `clip_sha256` (the withdrawn object), `reason` (`remask`), `mask_version`, `run_id`, and `withdrawn_utc`.
- [ ] **DATA-CLIP-003**: The system shall treat a clip as live when the latest current `clips` row for its `detection_id` has no current `clip_withdrawals` row with the same `clip_sha256`.

## Parquet Tables

- [ ] **DATA-PQ-001**: The system shall write each table with `pyarrow` and zstd compression from an explicit Arrow schema declared beside its Pydantic model, and a test shall assert that the two declarations agree.
- [ ] **DATA-PQ-002**: The system shall write `urbanbio.schema_version` into each Parquet file's key-value metadata, incrementing the minor version for an added nullable column and writing any other change as a new major version under a new table path.
- [ ] **DATA-PQ-003**: The system shall never modify or overwrite a Parquet file once written.
- [ ] **DATA-PQ-004**: The system shall write each table under the key prefix and Hive partitioning listed in data-model § Parquet Tables, with `year_month` the UTC `YYYY-MM` of the file start.
- [ ] **DATA-PQ-005**: The system shall name stage-written part files `<run_id>-<batch>.parquet`, `deployments` files `<deployment_id>-<run_id>.parquet` whichever run writes them, `waivers` files `<run_id>.parquet`, and other run-level table files (`media_retrievals`, `retrieval_anomalies`, `device_file_discards`, `geo_species_lists`, `sites`, `runs`) `<run_id>.parquet`.
- [ ] **DATA-PQ-006**: The system shall add `stage` and `unit_id` columns, naming the unit that wrote each row, to every table except the run-level tables and `stage_units`; `deployments` rows written by an `ingest` run carry `stage` `ingest` and a null `unit_id`.
- [ ] **DATA-PQ-007**: When a run of any stage ends and its generalized site configuration differs from the latest current `sites` file, the system shall write a new `sites` file holding generalized values only.

## Views and Querying

- [ ] **DATA-VIEW-001**: When `urbanbio.storage.tables.connect(store)` is called, the system shall return a DuckDB connection prepared by `store.configure_duckdb` with one view per table over `read_parquet(store.table_glob('curated/<table>/'), hive_partitioning = true, union_by_name = true)`.
- [ ] **DATA-VIEW-002**: The system shall register on that connection a `<table>_current` view per unit-scoped table (RUN-CURRENT-001) and the views `current_sites`, `current_masks`, `current_deployments`, `current_retrievals`, `audio_file_status`, and `audio_file_tier`.
- [ ] **DATA-VIEW-003**: The system shall compute latest-version reads only as DuckDB views, never as stored state.

## Interface-Level Entities

- [D] **DATA-IFACE-001**: The system shall define `Label`, `Calibration`, `WeatherObs`, `NoiseEvent`, and `DailySummary` models with the fields listed in data-model § Label, Calibration, WeatherObs, NoiseEvent, DailySummary, and their tables (`labels`, `calibrations`, `weather_obs`, `noise_events`, `daily_summary`) following data-model § Parquet Tables.
- [D] **DATA-IFACE-002**: The system shall compute `DailySummary.date_local` in the site's `timezone`.
