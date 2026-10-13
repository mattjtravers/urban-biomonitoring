# Runs Specs

EARS requirements for [runs](runs-design.md). Markers: `[x]` implemented, `[ ]` active gap,
`[D]` deferred.

## Units and Idempotency Keys

- [ ] **RUN-KEY-001**: The system shall identify each stage unit by the unit ID defined for its stage in runs § Units and Idempotency Keys: `<audio_file_id>` for `ingest`, `screen`, `speech-sample`, `speech-label`, and `purge`; `<audio_file_id>:v<mask_version>` for `archive` and `rescreen`; `<audio_file_id>:<model_id>` for `detect`; and `<deployment_id>`, or `<stage>:<unit_id>:<reason_code>` for a waiver, for `admin`.
- [ ] **RUN-KEY-002**: The system shall compute a unit's idempotency key as the SHA-256 of the canonical JSON of `stage`, `stage_version`, `unit_id`, `input_checksums`, `params_hash`, and `models`.
- [ ] **RUN-KEY-003**: The system shall serialize canonical JSON for hashing with `json.dumps(obj, sort_keys=True, separators=(",", ":"))`, with floats in `repr` form.
- [ ] **RUN-KEY-004**: The system shall set a unit's `input_checksums` to the original file's SHA-256 for `ingest`, `screen`, `speech-sample`, `archive`, and `purge`; the file's `original_pcm_sha256` for `detect`; the current `masked_sha256` for `rescreen`; the SHA-256 of the file's rows in `labels.csv` in canonical form for `speech-label`; and the SHA-256 of the canonical JSON of the command's arguments for `admin`.
- [ ] **RUN-KEY-005**: The system shall set a unit's `models` to the identifier and file SHA-256 of every model file the stage uses for `screen`, `detect`, and `rescreen`, and to `adapter:<key>:<version>` of the deployment's recorder adapter for `ingest`.
- [ ] **RUN-KEY-006**: The system shall set `params_hash` to the SHA-256 of the stage's parameters serialized as canonical JSON.
- [ ] **RUN-KEY-007**: The system shall take `stage_version` from an integer constant in each stage's module, and shall not include the git commit or dirty state in any idempotency key.

## Stage-Unit Ledger

- [ ] **RUN-LEDGER-001**: When a stage finishes a unit attempt, the system shall record one `stage_units` row with `stage`, `unit_id`, `idempotency_key`, `run_id`, `status`, `reason_code`, `waived_reason_codes`, `related_unit_id`, `output_refs`, `git_dirty` (copied from the run record), and `finished_utc`.

## Write Order

- [ ] **RUN-ORDER-001**: The system shall process each stage's units in batches of at most `RUN_BATCH_UNITS` (50) units.
- [ ] **RUN-ORDER-002**: When a batch's units are finished, the system shall write, in order: the batch's table part files `curated/<table>/<partitions>/<run_id>-<batch>.parquet`; then the ledger part file `curated/stage_units/stage=<stage>/<run_id>-<batch>.parquet`; then the batch's local cleanup (quarantine purges and work-directory deletions); and shall upload the run log to `runs/<run_id>.log` after the ledger part is written.
- [ ] **RUN-ORDER-003**: The system shall write a unit's store objects (archived FLACs, sidecars, clips) before the ledger part file that cites them.
- [ ] **RUN-ORDER-004**: The system shall not purge a quarantine original or delete a work-directory FLAC until the ledger rows recording the outputs that justify it are in the store.
- [ ] **RUN-ORDER-005**: When the `purge` stage deletes originals, the system shall write their `purge` ledger rows in the next batch's ledger part file, or at run end after the last batch.
- [ ] **RUN-ORDER-006**: When the archive stage purges originals, the system shall write their `purge` ledger rows under the archive run's `run_id` in the `stage=purge` partition, and `urbanbio purge` shall create its own `purge` run.

## Current Rows

- [ ] **RUN-CURRENT-001**: The system shall count a row of a unit-scoped table only when a `succeeded` `stage_units` row exists with the same `stage`, `unit_id`, and `run_id`.
- [ ] **RUN-CURRENT-002**: When a unit has `succeeded` in more than one run, the system shall treat the rows from the run with the latest `finished_utc` as the unit's current rows.
- [ ] **RUN-CURRENT-003**: The system shall decide whether a row counts only from its `stage`, `unit_id`, and `run_id` and the ledger, never from the row's other columns or the run's status.
- [ ] **RUN-CURRENT-004**: The system shall count rows of the run-level tables (`media_retrievals`, `retrieval_anomalies`, `geo_species_lists`, `sites`, `runs`, and the `deployments` rows an `ingest` run writes) only when their run's record has a status other than `running`.
- [ ] **RUN-CURRENT-005**: When a run writes a run-level table, the system shall write it once, at run end, computed over the run's whole scope including units completed by earlier runs.
- [ ] **RUN-CURRENT-006**: The system shall implement the current-rows rule once, in `urbanbio.storage.tables`, as a `<table>_current` view per unit-scoped table, and every reader of stage outputs shall read through those views.

## Lookups

- [ ] **RUN-LOOKUP-001**: Before executing a unit, the system shall look up its (`stage`, `idempotency_key`) in the ledger and skip the unit when the latest row is `succeeded`, skip it when the latest row is `flagged` and no waiver covers its reason code, and run it when the latest row is `failed` or no row exists.
- [ ] **RUN-LOOKUP-002**: While a run's `git_dirty` is false, the system shall treat a `succeeded` ledger row with `git_dirty: true` as absent and run the unit again when its input is still reachable.
- [ ] **RUN-LOOKUP-003**: The system shall provide an `audio_file_status` view that takes, per `audio_file_id`, the latest ledger row of each stage across all keys and reports the furthest status reached (data-model § AudioFile).

## Waivers

- [ ] **RUN-WAIVE-001**: When `urbanbio runs unflag STAGE UNIT --reason CODE --note TEXT` runs, the system shall create an `admin` run whose single unit writes a `waivers` row with `waived_stage`, `waived_unit_id`, `reason_code`, `note`, `run_id`, and `created_utc`.
- [ ] **RUN-WAIVE-002**: When a stage re-runs a unit covered by a waiver and a check would raise the waived reason code, the system shall log the code as a warning, continue the unit, and add the code to the unit's `waived_reason_codes`.
- [ ] **RUN-WAIVE-003**: When a unit covered by a waiver fails a check with a reason code the waiver doesn't name, the system shall flag the unit.
- [ ] **RUN-WAIVE-004**: The system shall match waivers to units by stage, unit ID, and reason code, not by idempotency key, so a waiver applies after stage-version or parameter changes.
- [ ] **RUN-WAIVE-005**: The system shall never record a `PrivacyError` as a unit-level flag, so no privacy check can be waived.

## Run Record

- [ ] **RUN-REC-001**: When a command creates a run, the system shall assign a `run_id` of the form `<stage>-<YYYYMMDDTHHMMSSZ>-<4 hex>` and write the run record to `runs/<run_id>.json` with status `running` before processing any unit.
- [ ] **RUN-REC-002**: When a run ends, the system shall rewrite `runs/<run_id>.json` with its final status and write its summary row to the `runs` table.
- [ ] **RUN-REC-003**: The system shall end a run `succeeded` when every unit was skipped or succeeded, `partial` when any unit was flagged or failed, and `failed` when a run-level error stopped it.
- [ ] **RUN-REC-004**: The system shall record in each run record the fields of runs § Run Record: `run_id`, `stage`, `status`, `started_utc`, `ended_utc`, `code` (`package_version`, `git_commit`, `git_dirty`, `stage_version`), `store` (`backend`, `store_id`), `environment`, `models`, `params`, `params_hash`, `scope`, `counts` (with per-ledger-stage counts in `by_stage`), `resources`, `errors`, `outputs`, and `log_key`.
- [ ] **RUN-REC-005**: The system shall record `resources.peak_rss_mb` as the largest resident set of the main process or any worker, and `resources.wall_clock_s` including time the host spent suspended.
- [ ] **RUN-REC-006**: If a run's parameters contain a key listed in `PRIVATE_KEYS` (`latitude_exact`, `longitude_exact`, `address`), then the system shall raise `PrivacyError` before writing the run record.
- [ ] **RUN-REC-007**: The system shall record run `errors` with `unit_id`, `reason_code`, and `message` only, with no audio and no coordinates.

## Dirty Runs

- [ ] **RUN-DIRTY-001**: When a command creates a run while the working tree has uncommitted changes, the system shall record `git_dirty: true` in the run record and in each of its ledger rows.
- [ ] **RUN-DIRTY-002**: While the profile is `processing`, when a command that creates a run starts with uncommitted changes in the working tree and without `--allow-dirty`, the system shall refuse with a `ConfigError` (exit `3`).
- [ ] **RUN-DIRTY-003**: While the profile is `processing`, the system shall accept `--allow-dirty` only on `ingest`, `screen`, `speech sample`, `archive`, `detect`, and `process`, and shall refuse it with a `ConfigError` on `rescreen`, `purge`, `deployment create`, `deployment end`, `runs unflag`, and `speech label --import`.
- [ ] **RUN-DIRTY-004**: When `archive` or `process` runs with `--allow-dirty`, the system shall behave as if `--no-purge` were given.
- [ ] **RUN-DIRTY-005**: While the profile is `dev`, the system shall allow commands that create runs to start with uncommitted changes, and every such command shall accept `--allow-dirty`.

## Concurrency

- [ ] **RUN-LOCK-001**: When a command that creates runs starts, the system shall take an exclusive lock file `<paths.state>/urbanbio.lock` holding its PID and `run_id`, and release it when the command ends.
- [ ] **RUN-LOCK-002**: If the lock is held by a live process, then the system shall exit with a `LockError` (exit `4`) naming the holder's PID and `run_id`.
- [ ] **RUN-LOCK-003**: If the lock's PID is not alive, then the system shall report the stale lock and exit with a `LockError` (exit `4`), unless `--force-unlock` is given, in which case it shall remove the lock and continue.

## Resource Limits

- [ ] **RUN-RES-001**: The system shall run `screen`, `archive`, and `detect` with at most `resources.workers` worker processes.
- [ ] **RUN-RES-002**: If `resources.workers` is less than 1, then the system shall raise a `ConfigError`.
- [ ] **RUN-RES-003**: The system shall have each `screen`, `archive`, and `detect` worker hold the samples and outputs of one audio file at a time, release them before taking the next file, and load its models once.
- [ ] **RUN-RES-004**: The system shall run `ingest`, `purge`, `speech-sample`, and `speech-label` in a single process.
- [ ] **RUN-RES-005**: If a worker process dies, then the system shall record its current unit as `failed` and continue the run with the remaining workers.

## Interrupted Runs

- [ ] **RUN-RESUME-001**: When a command is re-run after an interrupted run, the system shall skip units the ledger records as complete under the same idempotency key and process the rest.
- [ ] **RUN-RESUME-002**: When a batch's table part files exist but no ledger part file cites them, the system shall ignore those rows and repeat the batch's units under the new run's `run_id`.

## Deferred

- [D] **RUN-COMPACT-001**: When `stage_units` part files are numerous enough to slow lookups, the system shall compact them without changing which rows are current.
