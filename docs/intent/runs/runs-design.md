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

The design keeps one rule: **the outputs in AWS S3 are the ledger.** A stage skips a unit only
when AWS S3 shows that unit completed under the same idempotency key. Local disk holds no
authoritative state, so a lost or rebuilt machine loses nothing but caches and working copies.

Package: `urbanbio.provenance`.

## Units and Idempotency Keys

A **unit** is the smallest piece of work a stage can skip or redo:

| Stage | Unit | Unit ID |
|---|---|---|
| ingest | one audio file on a card | `audio_file_id` |
| screen | one audio file | `audio_file_id` |
| speech-sample | one audio file considered for the hold | `audio_file_id` |
| speech-label | one held audio file | `audio_file_id` |
| archive | one mask version of an audio file | `audio_file_id` + `mask_version` (1 at first archive) |
| purge | one audio file | `audio_file_id` |
| detect | one archived audio file | `audio_file_id` + `mask_version` |
| rescreen | one archived audio file | `audio_file_id` + `mask_version` |
| admin | one deployment change | `deployment_id` |

The **idempotency key** of a unit is the SHA-256 of a canonical JSON document:

```json
{
  "stage": "detect",
  "stage_version": 1,
  "unit_id": "af-3f9a0c1d2e4b5a6c",
  "input_checksums": ["<masked_sha256>"],
  "params_hash": "<sha256 of canonical params>",
  "models": ["birdnet-acoustic-2.4-fp32:<model file sha256>"]
}
```

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
| `output_refs` | `list[str]` | no | AWS S3 keys or table/file references |
| `finished_utc` | `datetime` | no | |

The two non-success statuses mean different things:

- **`failed`**: something went wrong that a retry may fix (a detector crashed, a network error
  after retries, a decode error). The next run retries the unit automatically.
- **`flagged`**: a check found something that needs a human decision (`truncated`,
  `outside_deployment`, `timestamp_mismatch`, `archive_conflict`, …). The unit is not retried
  until the maintainer waives the check (Tenet 3).

## Lookups

Two lookups read the ledger:

- **By key, to skip work.** Before executing a unit, a stage looks up
  `(stage, idempotency_key)`. A `succeeded` row means skip. A `flagged` row means skip, unless a
  waiver covers it (below). A `failed` row, or no row, means run.
- **By file, for status.** The `audio_file_status` view takes, for each `audio_file_id`, the
  latest row of each stage across all keys and reports the furthest stage reached (see
  [data-model](../data-model/data-model-design.md) § AudioFile). Ingest's duplicate check,
  purge's archive check, and `retrieval status` use this lookup.

## Waivers

`urbanbio runs unflag STAGE UNIT --reason CODE --note TEXT` writes a waiver row to the
`waivers` table: `stage`, `unit_id`, `reason_code`, `note`, `run_id`, `created_utc`. A waiver
covers exactly one reason code for one unit. When a stage re-runs that unit, a check that would
raise the waived code records it as a warning and continues, and the unit's ledger row carries
`waived_reason_codes`. Any other failing check still flags the unit. Waivers are keyed by unit
and reason code, not by idempotency key, so they keep applying after stage-version or parameter
changes. They are never removed.

Waivers are for checks whose condition the maintainer has confirmed is acceptable (for example,
a recording genuinely cut short by battery failure). Privacy checks can't be waived:
`PrivacyError` is never a unit-level flag
([config-cli](../config-cli/config-cli-design.md) § Errors).

The ledger is flushed to AWS S3 in batches (every 50 units and at the end of the run) as
immutable part files `curated/stage_units/stage=<stage>/<run_id>-<batch>.parquet`. If a run
crashes, the units completed before the last flush stay recorded, and the next run repeats at
most one batch.

## Run Record

Written to `runs/<run_id>.json` (full record) at the start of the run with status `running`,
and rewritten at the end. Its summary row goes to the `runs` table.

| Field | Type | Notes |
|---|---|---|
| `run_id` | `str` | `<stage>-<YYYYMMDDTHHMMSSZ>-<4 hex>` |
| `stage` | `str` | `admin`, `ingest`, `screen`, `speech-sample`, `speech-label`, `archive`, `purge`, `detect`, `rescreen`, … |
| `status` | `Literal["running", "succeeded", "partial", "failed"]` | `partial` when any unit was flagged or failed |
| `started_utc`, `ended_utc` | `datetime` | |
| `code` | object | `package_version`, `git_commit`, `git_dirty: bool`, `stage_version` |
| `environment` | object | `URBANBIO_ENV`, Python version, platform, versions of `birdnet`, `silero-vad`, `torch`, `soundfile`, libsndfile, `pyarrow`, `duckdb`, `boto3` |
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
`dev`, such runs are allowed. In `processing`, every command that creates a run refuses to start
with uncommitted changes (a `ConfigError`, exit `3`), so the processing machine runs only code
that is committed and has been through CI. `--allow-dirty` overrides the refusal for one
command, and the run still records `git_dirty: true`. Publish refuses to use outputs from dirty
runs in either environment ([publish](../publish/publish-design.md)).

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

The processing machine has little memory and no swap (the reference machine has 6.4 GB for
Linux; [environments](../environments/environments-design.md) § Reference machine). When it
runs out, the kernel kills a process. So the stages that process audio bound their memory
explicitly:

- **Worker count.** `screen`, `archive`, and `detect` run at most `resources.workers`
  workers, from the environment's config file. The default is 2 on the processing machine
  and 1 in development. The value must be between 1 and the CPU count; anything else is a
  `ConfigError`. Each stage LLD says what a worker is for that stage.
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

## Long Runs on the Processing Machine

Processing one weekly card takes hours. The processing machine's Linux environment suspends
when the host sleeps, and closing the terminal window ends the processes started in it. The
weekly card runbook therefore:

- keeps the machine plugged in and awake for the whole run (power settings: don't sleep while
  plugged in, and don't sleep when the lid is closed);
- starts `urbanbio process` inside `tmux`, so closing or losing the terminal window doesn't
  stop it.

A suspend pauses the run without losing work. AWS S3 requests in flight when the machine
sleeps fail on wake and are retried (botocore `standard` retries), and a unit that still fails
is retried by the next run. The ledger makes any interrupted run safe to restart: the same
command resumes and skips completed units. Time spent suspended is counted in
`wall_clock_s`, so the first-card measurement is taken with the machine awake throughout.

## Decisions & Alternatives

| Decision | Chosen | Alternatives Considered | Rationale |
|---|---|---|---|
| Source of truth for "done" | Ledger rows in AWS S3 | Local SQLite state; checking output object existence only | AWS S3 is the system of record (HLD §2), so state survives the loss of either environment. Object existence alone can't tell whether parameters changed. |
| What invalidates a unit | `stage_version`, params, inputs, models | git commit | Keying on the commit would reprocess the archive after every refactor, at real egress cost. A deliberate `stage_version` bump makes reprocessing a visible decision. |
| Run IDs | Time-based with random suffix | Deterministic from the idempotency key | A run is an execution event; two executions are two runs even when one skips everything. Determinism lives in unit keys. |
| Ledger write pattern | Batched immutable part files | One row per unit as separate objects; rewriting one file | Per-unit objects multiply request costs. Rewriting a shared file risks losing it on a crash. |
| Flagged units | Stay skipped until explicitly cleared | Retry automatically on the next run | Tenet 3: a unit that failed verification needs a human decision, not silent retries. |
| Memory bounding | Configured worker count, one file per worker | Workers equal to the CPU count; a memory-aware scheduler; adding swap | With no swap, the CPU count (8) would risk the kernel killing the run. A scheduler is complex for one machine. Swap is deferred until measured ([environments](../environments/environments-design.md) § Deferred). |
| Surviving a closed terminal | `tmux` | `nohup`; a systemd user service | `tmux` lets the maintainer reattach and watch progress. A service is setup for a weekly manual step (Tenet 4). |

## Open Questions & Future Decisions

### Deferred
1. Compaction of `stage_units` part files if their count makes lookups slow (expected only after
   several years).

## References

- HLD §2 (idempotency, provenance), §1.4 Tenets 2–3
- Weekly card procedure: `docs/runbooks/weekly-card.md`
