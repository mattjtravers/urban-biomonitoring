---
parent: high-level-design
prefix: CLI
---

# Configuration, CLI, and Package Foundation

## Context and Design Philosophy

This LLD covers what every stage shares: the package layout, declarative configuration with a
hard public/private split (HLD §6.2, §8), the `urbanbio` command line (HLD §2 "Execution
model"), error handling, logging, and the testing strategy.

Two principles drive it:

- **Private values never reach a stage.** Exact coordinates and account identifiers are loaded
  once, reduced (coordinates to generalized values), and kept out of every object passed to
  stage code, run records, and logs.
- **A stage is a function, and the CLI is a thin shell.** Each command parses arguments, loads
  configuration, opens a run ([runs](../runs/runs-design.md)), and calls one stage entry point.
  Tests call the same entry points without the CLI.

## Package Layout

```
src/urbanbio/
  __init__.py
  cli.py                 # Typer app; one command per stage plus admin commands
  config.py              # Config models, loading, private/public split
  errors.py              # Exception hierarchy and reason codes
  logging.py             # Console + JSON-lines file logging
  models/                # Pydantic models (data-model LLD)
    site.py deployment.py media.py speech.py detection.py
    validation.py covariates.py summary.py run.py
  storage/               # Store interface and backends, key layout, marker, Parquet tables (store LLD);
    store.py s3.py filesystem.py paths.py marker.py tables.py
    archive.py flac.py sidecar.py                 # archive (archive LLD)
  provenance/            # Run records, ledger, locking (runs LLD)
    runs.py ledger.py lock.py versions.py
  ingest/                # Intake and recorder adapters (ingest LLDs)
    intake.py purge.py timeutil.py riff.py guano.py
    adapters/base.py adapters/songmeter_micro2.py
  screen/                # Speech screen (speech-screen LLD)
    screen.py birdnet_human.py silero.py intervals.py mask.py recall.py
  detect/                # Detection (detect LLD)
    base.py birdnet_detector.py geo.py detect.py
  validate/ curate/ publish/   # Interfaces only in Phase 1
tests/
  unit/ integration/ fixtures/ (generators only; no audio files)
config/
  pipeline.toml          # Public, committed: stage parameters shared by every profile
  processing.toml        # Public, committed: processing-profile defaults (paths, resources, budget, store)
  dev.toml               # Public, committed: dev-profile defaults
  dev-sites.toml         # Public, committed: synthetic sites used by the dev profile
  private/               # Gitignored; scratch only (private values live only in the environment)
```

Module boundaries follow the HLD components. `models`, `storage`, `provenance`, and `config`
are shared. A stage package imports shared packages and never another stage's internals.
`storage.archive` imports `screen.mask` for the masking primitive, the only sanctioned
cross-stage import.

## Configuration

Format: TOML, read with `tomllib`, validated into Pydantic models (HLD §8).

### Profiles

A **profile** is a named set of deployment defaults (HLD §2). There are two:

- `processing` handles real recorder media.
- `dev` runs against synthetic sites and synthetic or public-domain audio.

The required variable `URBANBIO_PROFILE` names the profile. It has no default: when it is unset
or names an unknown profile, every command except `--help` exits with a `ConfigError`. Nothing
else selects or changes a profile; the software never inspects the host to choose one (HLD
Tenet 5).

Configuration is read in three layers:

1. `config/pipeline.toml`, the stage parameters. These are identical in every profile, so a
   result never depends on where it was computed (HLD G1).
2. `config/<URBANBIO_PROFILE>.toml`, the profile's deployment defaults. It may contain only the
   `[paths]`, `[resources]`, `[quarantine]`, and `[store]` tables (the **deployment tables**),
   and `pipeline.toml` may not contain them.
3. The **operator config file**, optional, named by `URBANBIO_CONFIG`. It may contain only
   deployment tables, and each key it sets replaces the profile's value for that key. The value
   must be an absolute path (after `~` expansion), and the file must exist, be a regular file,
   and lie outside the repository working tree; otherwise it is a `ConfigError`. A relative path
   is refused so the same variable can't select different files from different working
   directories. It holds an operator's own paths, worker count, budget, and store choice
   without editing committed files.

A key in the wrong layer is a `ConfigError`, so a stage parameter can't vary between profiles or
operators. Every deployment key has a documented default in its profile file, except
`store.root` in `processing`, which an operator choosing the `filesystem` backend must set.

### Public: `config/processing.toml` and `config/dev.toml` (committed)

```toml
# config/processing.toml
[paths]                            # `~` is expanded; no username is committed
quarantine = "~/urbanbio/quarantine"
work = "~/urbanbio/work"           # Masked FLAC staging and downloads
state = "~/urbanbio/state"         # Lock file, logs
model_cache = "~/.cache/urbanbio/birdnet"     # Exported as BIRDNET_APP_DATA
test_audio_cache = "~/.cache/urbanbio/test-audio"

[resources]                        # Worker count for audio stages (runs LLD)
workers = 2                        # Sized for 8 GB of RAM (install LLD § Sizing guidance)

[quarantine]                       # Disk budget (intake LLD § Disk Budget)
budget_gb = 35.0                   # Quarantine + work may use at most this
min_free_gb = 3.0                  # Refuse to start work below this

[store]                            # Store backend (store LLD)
backend = "s3"
region = "us-east-1"
```

```toml
# config/dev.toml
[paths]
quarantine = "~/urbanbio-dev/quarantine"   # Synthetic audio only
work = "~/urbanbio-dev/work"
state = "~/urbanbio-dev/state"
model_cache = "~/.cache/urbanbio/birdnet"
test_audio_cache = "~/.cache/urbanbio/test-audio"

[resources]
workers = 1

[quarantine]
budget_gb = 5.0                    # Synthetic data only
min_free_gb = 3.0

[store]
backend = "filesystem"
root = "~/urbanbio-dev/store"
```

An operator config file overriding only what differs, for example a lab share as the store and
a larger host:

```toml
# Outside the repository, e.g. ~/.config/urbanbio/config.toml; named by URBANBIO_CONFIG
[resources]
workers = 4

[quarantine]
budget_gb = 120.0

[store]
backend = "filesystem"
root = "/mnt/lab-share/urbanbio"
```

Every path is expanded (`~`) and resolved (symlinks followed), and it must lie outside the
repository working tree. A path inside the working tree is a `ConfigError`.

### The `dev` profile

The `dev` profile exists so the pipeline can be developed and tested without real data. It
refuses production data in three ways:

- **Synthetic sites.** Sites come from the committed `config/dev-sites.toml` (placeholder
  coordinates). When `URBANBIO_SITES` is set, every command exits with a `ConfigError`
  (`dev_private_sites`), so real coordinates are never loaded into a dev process.
- **Its own store.** The store marker must name `dev`
  ([store](../store/store-design.md) § Store Marker). A store initialized for `processing` is
  refused, on any backend.
- **No real media or archive writes.** The commands that touch real media or write to the
  archive (`ingest`, `process`, `archive`, `purge`, `rescreen`) exit with a `ConfigError` before
  doing anything. Tests exercise those stages by calling the stage entry points directly with a
  test store.

### Public: `config/pipeline.toml` (committed)

```toml
[recording]                        # Defaults for new deployments
sample_rate_hz = 48000
duty_on_s = 60
duty_off_s = 240
max_file_length_s = 60

[screen]
padding_s = 2.0
birdnet_mask_labels = ["Human vocal_Human vocal"]
birdnet_threshold = 0.10
birdnet_stats_floor = 0.02         # Scores kept for per-file maxima (near-miss sampling)
birdnet_overlap_s = 1.5
silero_threshold = 0.30
silero_min_speech_ms = 250
max_masked_fraction = 0.20         # Above this, the retrieval gets an anomaly

[detect]
model = "birdnet-acoustic-2.4"
backend = "litert"
precision = "fp32"
confidence_floor = 0.10
overlap_s = 0.0
sigmoid_sensitivity = 1.0
geo_model = "birdnet-geo-2.4"
geo_min_confidence = 0.03

[archive]
flac_compression_level = 0.6       # soundfile scale 0–1; lossless at any level
glacier_transition_days = 30
storage_class_on_write = "STANDARD"

[publish]
coordinate_precision_deg = 0.01
coordinate_uncertainty_m = 1000
```

### Private values

Private values come only from environment variables, in every profile (HLD §8). The code reads
them only from `os.environ` and never opens a file to find them. How they reach the environment
(an env file loaded by the shell, a secrets manager, a hosted development environment's secret
store) is the operator's choice, documented in runbooks.

| Variable | Profiles | Use |
|---|---|---|
| `URBANBIO_SITES` | `processing` (refused in `dev`) | Private site configuration (TOML, below) |
| `URBANBIO_BUCKET` | any, `s3` backend only | Private bucket name |
| `AWS_ACCESS_KEY_ID`, `AWS_SECRET_ACCESS_KEY` | any, `s3` backend only | Pipeline IAM user ([store](../store/store-design.md) § IAM) |

`URBANBIO_PROFILE` and `URBANBIO_CONFIG` are public. They are environment variables because
they select configuration, not because they are secret.

`URBANBIO_SITES` holds the exact site coordinates. Its value is a TOML document, in the same
format as `config/dev-sites.toml`:

```toml
[[site]]
site_id = "site1"
latitude_exact = 0.0       # Placeholder; real values exist only in the environment
longitude_exact = 0.0
habitat = "residential garden"
nws_station_id = "XXXX"
```

#### `urbanbio config check`

No configuration file in the repository contains credentials or the bucket name. `urbanbio
config check` validates configuration, private values, and the store for the current profile.

In every profile, it fails when:

- `URBANBIO_PROFILE` is unset or unknown, the configuration layers break the split above, or
  `URBANBIO_CONFIG` is relative, names a missing file, or names one inside the repository
  working tree;
- a variable required by the profile and backend is missing or empty;
- the site configuration doesn't parse, or it lacks a site that a deployment references;
- a public config value looks like a coordinate pair at a precision finer than
  `coordinate_precision_deg`;
- a configured path lies inside the repository working tree;
- the quarantine, work, or state directory is missing or has a mode other than `0700`;
- the store can't be opened: the store is unreachable, its marker is missing, or the marker
  names another profile ([store](../store/store-design.md) § Store Marker).

In `dev`, it also fails when `URBANBIO_SITES` is set.

In `processing` with the `filesystem` backend, it also fails when `store.root` is on the same
filesystem as `paths.quarantine` ([store](../store/store-design.md) § Same-Filesystem Rule).

Each failure prints the check and the fix, and never prints the value of a private variable.
The command exits `3` on any failure. It warns, without failing, when `resources.workers` exceeds the CPU count the
host reports ([runs](../runs/runs-design.md) § Resource Limits).

### Loading and the private boundary

`config.load()` returns a `PipelineConfig` (public fields plus `sites: list[Site]`). Private
`SiteSecret` objects (with exact coordinates) are used inside `config.load()` only to compute
each Site's generalized coordinates, then discarded. `PipelineConfig` has no attribute through
which exact coordinates can be reached. `SiteSecret.__repr__` masks coordinates so a traceback
can't print them.

Exact coordinates exist in two kinds of places, both private: the `URBANBIO_SITES` variable
(and wherever the operator keeps its value), and the positions recorders write into their own
file headers and logs, which survive only in the private store's sidecars and device logs
([archive](../archive/archive-design.md)). They never appear in tables, run records, or logs.

### Losing a host

No host holds anything authoritative. Results are in the store, the code is in git, and a card
isn't wiped until its files are archived
([intake](../ingest/intake/intake-design.md) § Card Wipe Readiness).

- **Processing host.** Losing it loses the quarantine, the work directory, and caches. A new
  host is set up with the installer ([install](../install/install-design.md)), and the operator
  restores private values from wherever they keep them. An unwiped card is ingested again.
- **Dev profile host.** Losing it loses only synthetic data and caches.

## CLI

Typer application `urbanbio`, installed as a console script.

| Command | Purpose | Run stage |
|---|---|---|
| `urbanbio config check` | Validate configuration, private values, directories, and the store | — |
| `urbanbio config paths` | Print the configured paths for the current profile, expanded and resolved | — |
| `urbanbio store init` | Write the store marker for the current profile ([store](../store/store-design.md) § Store Marker) | — |
| `urbanbio site list` | Show sites with generalized coordinates | — |
| `urbanbio deployment create --site S --serial N --recorder-name R --start T [--adapter A]` | Register a deployment; refuses to overlap an active deployment on the same device | `admin` |
| `urbanbio deployment end DEPLOYMENT --end T` | Close a deployment | `admin` |
| `urbanbio ingest PATH --deployment D --retrieved T [--clock-offset-s X] [--retrieval R]` | Hash, parse, and register a card copied into the quarantine | `ingest` |
| `urbanbio screen --retrieval R` | Run both speech detectors; write speech events | `screen` |
| `urbanbio archive --retrieval R [--no-purge]` | Mask, encode FLAC, write to the store, verify; purge each verified original unless held or `--no-purge` | `archive` |
| `urbanbio detect --retrieval R \| --all-pending` | Run BirdNET on archived audio | `detect` |
| `urbanbio purge --retrieval R [--discard UNIT … --reason TEXT]` | Delete verified (or superseded) originals from the quarantine; discard named flagged files without archiving | `purge` |
| `urbanbio process PATH --deployment D --retrieved T [--clock-offset-s X] [--speech-sample N]` | `ingest → screen → [speech sample] → archive (purging each original once verified) → detect` | one run per stage |
| `urbanbio retrieval status R` | Per-file status; tells whether the card can be wiped | — |
| `urbanbio speech sample --retrieval R --n N` | Hold a stratified sample in quarantine for speech labeling | `speech-sample` |
| `urbanbio speech label --import` | Import labels for held samples from `labels.csv` and release the holds | `speech-label` |
| `urbanbio speech recall` | Report speech recall and masked fraction | — |
| `urbanbio rescreen --retrieval R` | Re-screen archived audio and re-mask | `rescreen` |
| `urbanbio runs list [--stage S]` / `runs show RUN` / `runs unflag STAGE UNIT` | Provenance | — |
| `urbanbio query` | Open a DuckDB shell with curated views registered | — |

Every command that creates a run accepts `--allow-dirty`
([runs](../runs/runs-design.md) § Run Record).

Exit codes: `0` success; `1` run failed; `2` run partial (some units flagged or failed);
`3` configuration error (including the store marker); `4` lock held.

## Errors

```
UrbanbioError
├── ConfigError            # bad or missing configuration, missing secrets, store marker
├── PrivacyError           # a guard tripped (unmasked audio outside quarantine, private key in params)
├── IntegrityError         # checksum or bit-identity mismatch
├── AdapterError           # unparseable or inconsistent device data
├── TimeNormalizationError # missing or contradictory time-zone information
├── StorageError           # store failure after retries
└── LockError
```

Every error carries a `reason_code` (a short constant such as `checksum_mismatch`,
`guano_missing_timestamp`) used in the ledger and run record.

- **Unit-level check failures** (`IntegrityError`, `AdapterError`, `TimeNormalizationError`)
  flag that unit for a human decision, record the reason, and the run continues with other
  units (Tenet 3). The run ends `partial`.
- **Unit-level transient failures** (a model or detector raising, a file that fails to decode,
  a store error after retries on one object) mark that unit `failed`. The next run retries it
  ([runs](../runs/runs-design.md) § Stage-Unit Ledger). The run ends `partial`.
- **Run-level errors** (`ConfigError`, `PrivacyError`, `LockError`, and `StorageError` after
  retries) stop the run immediately with status `failed`.
- **Store retries** are each backend's own ([store](../store/store-design.md)).
- A `PrivacyError` is never caught by stage code.

## Logging

Standard library `logging`:

- Console: human-readable, `INFO` by default, `--verbose` for `DEBUG`.
- File: JSON lines at `<paths.state>/logs/<run_id>.log` with `ts_utc`, `level`, `run_id`,
  `stage`, `unit_id`, `event`, `message`, plus structured fields. Written to the
  store at `runs/<run_id>.log` when the run ends.
- Logs contain IDs, offsets, counts, and checksums, never audio samples, exact coordinates,
  credentials, or the bucket name. A logging filter redacts values of `URBANBIO_BUCKET`,
  `URBANBIO_SITES`, and `AWS_*` environment variables if they appear in a message. Store
  locations are logged as store-relative keys.

## Dependencies

Pinned in `pyproject.toml`, locked in `uv.lock`. Python 3.13 (the newest version with LiteRT
wheels), pinned in `.python-version`. The uv version is pinned too
([install](../install/install-design.md)).

| Package | Purpose |
|---|---|
| `birdnet==1.1.1` | BirdNET V2.4 acoustic and geo models on LiteRT ([detect](../detect/detect-design.md)) |
| `silero-vad==6.2.3` | Voice-activity detection ([speech-screen](../speech-screen/speech-screen-design.md)) |
| `torch` (CPU wheels from the PyTorch CPU index) | Required by `silero-vad`; CPU-only to save about 2 GB of disk |
| `soundfile` | WAV/FLAC I/O via libsndfile |
| `scipy` | Resampling (also a `birdnet` dependency) |
| `numpy`, `pydantic`, `typer`, `boto3`, `pyarrow`, `duckdb` | Core |
| dev: `pytest`, `ruff`, `moto[s3]` | Tests and lint |

`uv` installs the CPU-only `torch` via a `[tool.uv.sources]` entry pointing at
`https://download.pytorch.org/whl/cpu`.

## Testing Strategy

- **No real field audio in the repository, ever.** CI's `repo-hygiene` job rejects audio by
  extension and content. Test audio is **generated at test time** by fixture helpers in
  `tests/fixtures/` (tones, chirps, noise, silence), written to `tmp_path`.
- **Synthetic Song Meter cards.** A fixture builder writes WAV files with a `guan` chunk, an
  extra vendor chunk, a `Data/` folder, and a `_Summary.txt` in the documented format. It can
  emit edge cases: header rows repeated after a reboot, missing files, a daylight-saving
  boundary, inconsistent timestamps.
- **Speech detector tests** inject detector outputs through the detector interface (fakes)
  to test interval logic, padding, merging, and masking exactly. Tests that run the real
  Silero and BirdNET models use public-domain speech (LibriVox recordings) downloaded at test
  time from a pinned URL, checked against a pinned SHA-256, and cached in
  `paths.test_audio_cache`. These tests carry `@pytest.mark.models` and are opt-in
  (`uv run pytest -m models`), not run in CI by default.
- **Stores.** Unit and integration tests use a `filesystem` store in `tmp_path` or an `s3`
  store mocked with `moto`. The store contract suite runs against both
  ([store](../store/store-design.md) § Store Interface). No test touches a real bucket or a
  real share.
- **Real-card checks** (DST behavior, actual header layout) are verified by the operator
  against the first real cards using `urbanbio` commands. Findings are written into the
  [songmeter-micro2](../ingest/songmeter-micro2/songmeter-micro2-design.md) LLD and encoded as
  synthetic fixtures, never as copies of real files.
- Every test cites the EARS IDs it verifies with `# @spec` comments.

## Decisions & Alternatives

| Decision | Chosen | Alternatives Considered | Rationale |
|---|---|---|---|
| Config format | TOML + Pydantic | YAML; environment variables only | `tomllib` is in the standard library; Pydantic gives typed validation. Secrets alone come from the environment (HLD §8). |
| Profile selection | Required `URBANBIO_PROFILE`, no default | Detect the host (e.g., a hosted development environment's variables); default to `dev` | A wrong guess would put real-media paths in the wrong place (Tenet 3), and host detection ties behavior to hosts rather than configuration (Tenet 5). |
| Per-profile config | Shared `pipeline.toml` plus `<profile>.toml` restricted to deployment tables | One complete file per profile | Stage parameters can't drift between profiles, so the same inputs give the same results wherever they run. |
| Operator overrides | Optional `URBANBIO_CONFIG` file outside the working tree, deployment tables only | Edit the committed profile files in a fork; environment variables per key | Operators set their own paths, budget, and store without carrying a diff against the repository, and without a variable per key. Restricting it to deployment tables keeps stage parameters identical everywhere. |
| Dev-profile sites | Committed synthetic sites; `URBANBIO_SITES` refused | Accept `URBANBIO_SITES` in dev | Development never needs real coordinates, and refusing the variable means they can't be loaded into a dev process by accident. |
| Bucket name | Environment variable | Private config file | The bucket name isn't committed and arrives the same way as the other private values. |
| Exact coordinates | TOML in an environment variable, reduced at load time | Private TOML file read by the code; copy in the private store with backup/restore commands | Follows the rule that private values come only from the environment, keeps one code path for every profile, and means the code never reads a private file it might log or copy. Editing is less convenient, but sites change rarely. |
| Private-value delivery | Environment variables only; delivery is the operator's choice | Read an env file or keyring from the code; AWS shared credential files | One code path for every host. Code that opens a secrets file can log or copy it, and fixing one delivery method excludes operators who use another. |
| CLI framework | Typer | argparse; Click | Type-hinted commands with little code. Click is its dependency anyway. |
| Real-model tests | Opt-in, public-domain audio downloaded at test time | Commit small public-domain clips; mocks only | Keeps the repository audio-free (CI guard stays absolute) while still exercising real models. |
| torch build | CPU-only index | Default PyPI wheel | The default Linux wheel pulls CUDA libraries (several GB) that the CPU-only pipeline never uses, onto hosts sized from about 50 GB of disk. |

## Open Questions & Future Decisions

### Deferred
1. A third profile (e.g., `reprocess`, reading the archive without touching media) would add a
   `config/<profile>.toml` and a value of `URBANBIO_PROFILE`.

## References

- HLD §2, §6.2, §8
- [install](../install/install-design.md): prerequisites, installer, package list, version pins
- [store](../store/store-design.md): store backends and marker
- Typer: https://typer.tiangolo.com/
- uv PyTorch integration: https://docs.astral.sh/uv/guides/integration/pytorch/
- moto: https://docs.getmoto.org/
