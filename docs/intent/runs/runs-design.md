---
parent: high-level-design
prefix: RUN
---

# Runs, Provenance, and Idempotency

## Context and Design Philosophy

Every stage execution produces a **run record** (HLD §2 "Provenance") so any published number
traces to its inputs, code, model, and parameters (G1). Every stage is also **idempotent**:
re-running on the same inputs with the same parameters and versions does no work (HLD §2
"Idempotency").

The design keeps one rule: **the outputs in the store are the ledger.** A stage skips a unit
only when the store ([store](../store/store-design.md)) shows that unit completed under the
same idempotency key. Local disk holds no authoritative state, so a lost or rebuilt host loses
nothing but caches and working copies.

Package: `urbanbio.provenance`.

## Units and Idempotency Keys

A **unit** is the smallest piece of work a stage can skip or redo:

| Stage | Unit | Unit ID |
|---|---|---|
| ingest | one audio file on a card | `audio_file_id` |
| screen | one audio file | `audio_file_id` |
| speech-sample | one audio file considered for the hold | `audio_file_id` |
| speech-label | one held audio file | `audio_file_id` |
| archive | one mask version of an audio file | `<audio_file_id>:v<mask_version>` (`v1` at first archive) |
| purge | one audio file | `audio_file_id` |
| detect | one archived audio file under one model | `<audio_file_id>:<model_id>` |
| rescreen | one archived audio file at its current mask version | `<audio_file_id>:v<mask_version>` (the version re-screened) |
| admin | one deployment change, or one waiver | `deployment_id`, or `<stage>:<unit_id>:<reason_code>` |

A unit ID that combines values joins them with `:`, which no entity ID contains (entity IDs
match `^[a-z0-9-]+$`), so a unit ID splits back into its parts unambiguously.
| validate, publish | defined with their specs | Both stages are interfaces only in Phase 1. Their units (for example, the units that cut clips and write the `clips` table) are defined when their specs are written, and must give every row they write a `stage` and `unit_id`. |

The **idempotency key** of a unit is the SHA-256 of a canonical JSON document:

```json
{
  "stage": "detect",
  "stage_version": 1,
  "unit_id": "af-3f9a0c1d2e4b5a6c",
  "input_checksums": ["<original_pcm_sha256>"],
  "params_hash": "<sha256 of canonical params>",
  "models": ["birdnet-acoustic-2.4-fp32:<model file sha256>"]
}
```

- `input_checksums` holds the unit's input: the original file's SHA-256 for stages that read
  originals, `original_pcm_sha256` for `detect` (see [detect](../detect/detect-design.md)), the
  current `masked_sha256` for `rescreen`, and for `speech-label` the SHA-256 of the file's rows
  in `labels.csv` in canonical form, so importing the same labels again is a no-op and changed
  labels re-run the unit.
- `models` lists every versioned component whose output the stage depends on: model files for
  `screen`, `detect`, and `rescreen`, and the recorder adapter (`adapter:<key>:<version>`) for
  `ingest`.
- `stage_version` is an integer constant in each stage's module. It increments when a code
  change alters that stage's outputs. Other code changes leave keys unchanged, so a refactor
  doesn't trigger reprocessing; the git commit is still recorded in the run.
- A `stage_version` bump only affects units the stage can still reach. `screen` and
  `archive` read quarantine originals, so a bump to either re-processes only files not yet
  purged. Purged files are reprocessed through `rescreen` (from the archive), which has its own
  `stage_version`. `detect` reads the archive, so a bump to it covers every archived file.
- `params_hash` is the SHA-256 of the stage's parameters serialized as canonical JSON (sorted
  keys, no whitespace, floats in `repr` form).
- Canonical JSON for hashing uses `json.dumps(obj, sort_keys=True, separators=(",", ":"))`.

## Stage-Unit Ledger

The `stage_units` table (see [data-model](../data-model/data-model-design.md)) records one row
per unit attempt:

| Field | Type | Null | Notes |
|---|---|---|---|
| `stage` | `str` | no | Partition key |
| `unit_id` | `str` | no | |
| `idempotency_key` | `str` | no | |
| `run_id` | `str` | no | |
| `status` | `Literal["succeeded", "flagged", "failed"]` | no | |
| `reason_code` | `str` | yes | For `flagged` and `failed` |
| `waived_reason_codes` | `list[str]` | no | Checks waived for this attempt; usually empty |
| `related_unit_id` | `str` | yes | E.g. the superseding file for `superseded` |
| `output_refs` | `list[str]` | no | Store keys or table/file references |
| `git_dirty` | `bool` | no | Copied from the run record (§ Run Record) |
| `finished_utc` | `datetime` | no | |

The two non-success statuses mean different things:

- **`failed`**: something went wrong that a retry may fix (a detector crashed, a network error
  after retries, a decode error). The next run retries the unit automatically.
- **`flagged`**: a check found something that needs a human decision (`truncated`,
  `outside_deployment`, `timestamp_mismatch`, `archive_conflict`, …). The unit is not retried
  until the operator waives the check (Tenet 3).

## Write Order

A stage persists a unit's results in a fixed order, so a crash at any point leaves the store
consistent and the next run repairs it:

1. **Outputs.** The unit's store objects (archived FLACs, sidecars, clips) and its table rows
   are written. Table rows are written as part files per run × batch (below).
2. **Ledger.** The ledger rows for the same batch are written, after every output they cite.
3. **Local cleanup.** Only then does the stage take a destructive local action that depends on
   the unit: purging an original from the quarantine, or deleting a work-directory FLAC.

The main process works in **batches** of at most `RUN_BATCH_UNITS` (50) units. When a batch's
units are finished, it writes the batch's table part files
`curated/<table>/<partitions>/<run_id>-<batch>.parquet` (one per table and partition the batch
touches), then the ledger part file
`curated/stage_units/stage=<stage>/<run_id>-<batch>.parquet`, then performs the batch's local
cleanup. The run log is uploaded at the same point (`runs/<run_id>.log`, rewritten each batch).

If a run crashes:

- before a batch's ledger part is written, that batch's table part files are **orphans**: the
  ledger doesn't cite them, every reader ignores them (§ Current Rows), and the next run
  repeats the batch's units under its own `run_id`;
- after the ledger part is written but before local cleanup, the next run finds the units
  `succeeded` and only repeats the cleanup (purge and work-directory sweeps are idempotent).

Purge is the one stage whose output is a local action. It deletes first and then writes its own
ledger rows in the next batch write, or at run end after the last batch; a purge unit whose original is already gone, and whose
archive success is in the store, is recorded as `succeeded`, so a crash between the deletion and
the ledger write repairs itself ([intake](../ingest/intake/intake-design.md) § Purge).

So no original is ever purged, and no work FLAC deleted, before the store records the outputs
that justify it, and a crash never yields duplicate rows that readers count.

## Current Rows

A table row produced by a stage counts only when the ledger records its unit as `succeeded`
under the same `run_id`. Every unit-scoped table carries the `stage` and `unit_id` of the unit
that wrote each row ([data-model](../data-model/data-model-design.md) § Parquet Tables), and the
views join rows to the ledger on (`stage`, `unit_id`, `run_id`). Nothing is inferred from a row's
other columns: a re-mask's `audio_mask_versions` row, for example, has `mask_version` 2 but
belongs to the `rescreen` unit for mask version 1. When a unit has succeeded more than once (for example, after a
parameter change), the row set from the latest such run (by `finished_utc`) is the **current**
one. Every reader (views, curate, validate, publish) reads stage outputs through this rule, which
`urbanbio.storage.tables` implements as one view per table (e.g. `detections_current`,
`speech_events_current`); see [data-model](../data-model/data-model-design.md) § Parquet Tables.

Run status doesn't enter the rule. A run that ends `partial` because some units were flagged
still produces current rows for every unit that succeeded; a run that crashed (`running`
forever) does too, for every batch whose ledger part was written.

**Run-level tables.** A few tables hold rows about a run or a retrieval rather than about one
unit: `media_retrievals`, `geo_species_lists`, `sites`, and `runs`. Each is written once, when the
run ends. Its rows count when the run's record has ended (any status other than `running`). A
crashed run's run-level rows are never written or are ignored, and the run that resumes the work
writes them again, computed over the whole scope (for `media_retrievals`, every file of the part,
including those an earlier run completed).

## Lookups

Two lookups read the ledger:

- **By key, to skip work.** Before executing a unit, a stage looks up
  `(stage, idempotency_key)`. A `succeeded` row means skip, except as below. A `flagged` row
  means skip, unless a waiver covers it (below). A `failed` row, or no row, means run.
  A run from committed code (`git_dirty: false`) treats a `succeeded` row with `git_dirty: true`
  as no row and runs the unit again when its input is still reachable, so results from
  uncommitted code are replaced at the next clean run (§ Run Record).
- **By file, for status.** The `audio_file_status` view takes, for each `audio_file_id`, the
  latest row of each stage across all keys and reports the furthest stage reached (see
  [data-model](../data-model/data-model-design.md) § AudioFile). Ingest's duplicate check,
  purge's archive check, and `retrieval status` use this lookup.

## Waivers

`urbanbio runs unflag STAGE UNIT --reason CODE --note TEXT` is an `admin` run whose single
unit writes a waiver row to the `waivers` table: `waived_stage`, `waived_unit_id`,
`reason_code`, `note`, `run_id`, `created_utc` (plus the `stage` and `unit_id` of the `admin`
unit that wrote it, like every unit-scoped table). A waiver
covers exactly one reason code for one unit. When a stage re-runs that unit, a check that would
raise the waived code records it as a warning and continues, and the unit's ledger row carries
`waived_reason_codes`. Any other failing check still flags the unit. Waivers are keyed by unit
and reason code, not by idempotency key, so they keep applying after stage-version or parameter
changes. They are never removed.

Waivers are for checks whose condition the operator has confirmed is acceptable (for example,
a recording genuinely cut short by battery failure). Privacy checks can't be waived:
`PrivacyError` is never a unit-level flag
([config-cli](../config-cli/config-cli-design.md) § Errors).

## Run Record

Written to the store at `runs/<run_id>.json` (full record) at the start of the run with status `running`,
and rewritten at the end. Its summary row goes to the `runs` table.

| Field | Type | Notes |
|---|---|---|
| `run_id` | `str` | `<stage>-<YYYYMMDDTHHMMSSZ>-<4 hex>` |
| `stage` | `str` | `admin`, `ingest`, `screen`, `speech-sample`, `speech-label`, `archive`, `purge`, `detect`, `rescreen`, … |
| `status` | `Literal["running", "succeeded", "partial", "failed"]` | `partial` when any unit was flagged or failed |
| `started_utc`, `ended_utc` | `datetime` | |
| `code` | object | `package_version`, `git_commit`, `git_dirty: bool`, `stage_version` |
| `store` | object | `backend` and `store_id` from the store marker ([store](../store/store-design.md) § Store Marker); never the bucket name or root path |
| `environment` | object | `URBANBIO_PROFILE`, Python version, platform, versions of `birdnet`, `silero-vad`, `torch`, `soundfile`, libsndfile, `pyarrow`, `duckdb`, `boto3` |
| `models` | list | `model_id`, `version`, `backend`, `precision`, `file_sha256` |
| `params` | object | Stage parameters (public-safe; see below) |
| `params_hash` | `str` | |
| `scope` | object | What was requested, e.g. `{"retrieval_id": "site1-dep001-r004"}` |
| `counts` | object | `units_total`, `skipped`, `succeeded`, `flagged`, `failed` |
| `resources` | object | `workers`, `cpu_count`, `mem_total_mb`, `peak_rss_mb` (largest resident set of the main process or any worker), `wall_clock_s` |
| `errors` | list | `unit_id`, `reason_code`, `message` (no audio, no coordinates) |
| `outputs` | list | Tables and key prefixes written |
| `log_key` | `str` | `runs/<run_id>.log` |

A run started with uncommitted changes in the working tree records `git_dirty: true`. In
the `dev` profile, such runs are allowed. In the `processing` profile, every command that creates
a run refuses to start with uncommitted changes (a `ConfigError`, exit `3`), so real media is
processed only by code that is committed and has been through CI. `--allow-dirty` overrides the
refusal for one command, and the run still records `git_dirty: true`. Publish refuses to use
outputs from dirty runs in any profile ([publish](../publish/publish-design.md)).

Results from a dirty run must stay replaceable, so in the `processing` profile:

- `--allow-dirty` is accepted only by commands whose results a clean run can replace: `ingest`,
  `screen`, `speech sample`, `archive`, `detect`, and `process`. `rescreen` (its overwrite
  removes the previous archived version), `purge` (deletion can't be redone), `deployment
  create`/`end` and `runs unflag` (`admin`; no clean re-run exists), and `speech label --import`
  (labels can't be re-derived) refuse it with a `ConfigError`.
- `--allow-dirty` implies `--no-purge` for `archive` and `process`, and purge refuses to delete an
  original any of whose current `ingest`, `screen`, or `archive` successes came from a dirty run
  ([intake](../ingest/intake/intake-design.md) § Purge). The originals stay in the quarantine
  until a clean run has re-run those stages on them.
- A clean run re-runs every unit whose current success came from a dirty run, when its input is
  still reachable (§ Lookups). If a clean `archive` produces different samples from the dirty
  run's archived object, the unit is flagged `archive_conflict`, which the operator resolves with
  the administrator identity ([archive](../archive/archive-design.md) § Archive Flow).

**Public-safe parameters.** Run parameters may contain generalized coordinates but never exact
ones. The configuration layer never exposes exact coordinates to stages
([config-cli](../config-cli/config-cli-design.md)), and the run writer rejects any parameter
whose key is listed in `PRIVATE_KEYS` (`latitude_exact`, `longitude_exact`, `address`).

## Concurrency

Only one pipeline command that writes runs at a time. Commands that create runs take an
exclusive lock file `<paths.state>/urbanbio.lock` holding the PID and `run_id`. A second
command exits with an error naming the holder. A stale lock (PID not alive) is reported and
removed only with `--force-unlock`.

## Resource Limits

A processing host may have little memory and no swap. When memory runs out, the kernel kills a
process. So the stages that process audio bound their memory explicitly, from configuration
rather than from what they can detect about the host (HLD Tenet 5):

- **Worker count.** `screen`, `archive`, and `detect` run at most `resources.workers`
  workers, from the profile's configuration
  ([config-cli](../config-cli/config-cli-design.md) § Configuration). The default is 2 in the
  `processing` profile, sized for a host with 8 GB of RAM
  ([install](../install/install-design.md) § Sizing guidance), and 1 in `dev`. The value must
  be at least 1; anything else is a `ConfigError`. `config check` warns, without failing, when
  it exceeds the CPU count the host reports. Each stage LLD says what a worker is for that stage.
- **One file at a time per worker.** A worker holds the samples of one audio file, and that
  file's outputs, and releases them before taking the next. No stage loads a batch, a day, or
  a retrieval of audio into memory at once. Models are loaded once per worker.
- **Other stages** (`ingest`, `purge`, `speech-sample`, `speech-label`) are I/O-bound and run
  in a single process. `ingest` hashes in 1 MiB blocks and decodes one file at a time.
- **A killed worker** loses only its current unit. That unit is marked `failed` and retried
  by the next run, and the run ends `partial`. When the main process itself is killed, the
  run record stays `running` and the lock is stale. The same command, with `--force-unlock`,
  resumes the work, and the ledger skips completed units.

Every run records its worker count, peak memory, and wall-clock time (`resources` in the run
record), so the limits can be tuned from evidence. **TODO: measure on the first real card**:
peak memory and wall-clock time per stage for one card at the default worker count, and
whether 3 or 4 workers stay within memory with a margin of at least 1 GB. Raise the default
only on that evidence.

## Long and Interrupted Runs

Processing one weekly card takes hours. During that time the host may suspend, lose power, or
lose the terminal that started the command. Keeping the host awake and the command attached to a
session that outlives its terminal are operator concerns, covered in the deployment runbooks.
The software's part is to make every interruption safe:

- A suspend pauses the run without losing work. Store requests in flight when the host sleeps
  fail on wake and are retried by the backend, and a unit that still fails is marked `failed`
  and retried by the next run.
- Any interrupted run is safe to restart: the same command resumes, and the ledger skips
  completed units. A stale lock left by a killed main process is cleared with `--force-unlock`
  (§ Concurrency).
- Time spent suspended is counted in `wall_clock_s`, so resource measurements are taken with the
  host awake throughout.

## Decisions & Alternatives

| Decision | Chosen | Alternatives Considered | Rationale |
|---|---|---|---|
| Source of truth for "done" | Ledger rows in the store | Local SQLite state; checking output object existence only | The store is the system of record (HLD §2), so state survives the loss of any host. Object existence alone can't tell whether parameters changed. |
| What invalidates a unit | `stage_version`, params, inputs, models | git commit | Keying on the commit would reprocess the archive after every refactor, at real egress cost. A deliberate `stage_version` bump makes reprocessing a visible decision. |
| Run IDs | Time-based with random suffix | Deterministic from the idempotency key | A run is an execution event; two executions are two runs even when one skips everything. Determinism lives in unit keys. |
| Ledger write pattern | Batched immutable part files | One row per unit as separate objects; rewriting one file | Per-unit objects multiply request costs. Rewriting a shared file risks losing it on a crash. |
| Persist order | Outputs, then ledger, then local cleanup, per batch | Purge each original as soon as its write is confirmed; ledger at end of run | Purging before the ledger is in the store can delete an original whose archival the store can't show, leaving a retrieval that can never be confirmed safe to wipe. |
| Duplicate rows after a crash | Rows count only when cited by a succeeded ledger row | Deterministic, run-independent file names that a retry overwrites | Run IDs are per execution, so names can't repeat across a retry without overwriting objects; ledger citation needs no overwrite and also covers a crash between output and ledger writes. |
| Which rows are current | Latest succeeded run per unit | Latest succeeded run per stage or model | Runs cover one retrieval or a pending set, so a per-run choice would select only part of the data; partial runs are normal when any unit is flagged. |
| Dirty-run results | Re-run by the next clean run; originals kept until then; refused where no re-run is possible | Put `git_dirty` in the idempotency key; accept dirty results permanently | Keying on dirtiness would also re-run clean results after a dirty run. Without a re-run, outputs from a dirty run could never be published, and once originals were purged they could never be redone. |
| Flagged units | Stay skipped until explicitly cleared | Retry automatically on the next run | Tenet 3: a unit that failed verification needs a human decision, not silent retries. |
| Memory bounding | Configured worker count with a conservative default, one file per worker | Workers equal to the detected CPU count; a memory-aware scheduler | On a small host without swap, one worker per core can exhaust memory and get the run killed. A scheduler is complex for one host. A configured count is predictable and follows Tenet 5. |

## Open Questions & Future Decisions

### Deferred
1. Compaction of `stage_units` part files if their count makes lookups slow (expected only after
   several years).

## References

- HLD §2 (idempotency, provenance), §1.4 Tenets 2, 3, and 5
- Weekly card procedure: `docs/runbooks/weekly-card.md`
