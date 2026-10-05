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
  storage/               # AWS S3 access, key layout, Parquet tables, archive (archive LLD)
    s3.py paths.py tables.py archive.py flac.py sidecar.py
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
  pipeline.toml          # Public, committed
  private/               # Gitignored; scratch only (private config lives in a Codespaces secret)
```

Module boundaries follow the HLD components. `models`, `storage`, `provenance`, and `config`
are shared. A stage package imports shared packages and never another stage's internals.
`storage.archive` imports `screen.mask` for the masking primitive, the only sanctioned
cross-stage import.

## Configuration

Format: TOML, read with `tomllib`, validated into Pydantic models. One public file per
environment (HLD §8). There is a single environment today, so there is one file.

### Public: `config/pipeline.toml` (committed)

```toml
[paths]
quarantine = "/workspaces/quarantine"
work = "/workspaces/work"          # Masked FLAC staging and downloads; outside the repo
state = "/workspaces/state"        # Lock file, logs, caches
model_cache = "/workspaces/.cache/birdnet"   # Exported as BIRDNET_APP_DATA

[aws]
region = "us-east-1"
# Bucket name comes from the environment (URBANBIO_BUCKET).

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

[quarantine]
min_free_gb = 3.0                  # Refuse to start work below this
```

### Private: the `URBANBIO_SITES` Codespaces secret

Exact site coordinates are held in a user-level Codespaces secret, `URBANBIO_SITES`, scoped to
this repository. Its value is a TOML document:

```toml
[[site]]
site_id = "site1"
latitude_exact = 0.0       # Placeholder; real values exist only in the secret
longitude_exact = 0.0
habitat = "residential garden"
nws_station_id = "XXXX"
```

The secret belongs to the maintainer's GitHub account, not to any Codespace, so it survives
Codespace deletion (GitHub deletes Codespaces that stay stopped for 30 days). It is set from a
temporary file that is deleted afterwards:

```bash
gh secret set URBANBIO_SITES --user --app codespaces \
  --repos mattjtravers/urban-biomonitoring < sites.toml && rm sites.toml
```

GitHub never shows a secret's value again. It can be read only from the environment of a
running Codespace, and a Codespace must be restarted to see an updated value. Secret values
are limited to 48 KB, far more than a few sites need.

### Environment (Codespaces secrets)

| Variable | Use |
|---|---|
| `AWS_ACCESS_KEY_ID`, `AWS_SECRET_ACCESS_KEY` | Pipeline IAM user ([archive](../archive/archive-design.md) § IAM) |
| `AWS_DEFAULT_REGION` | `us-east-1` |
| `URBANBIO_BUCKET` | Private bucket name |
| `URBANBIO_SITES` | Private site configuration (TOML, above) |

No configuration file in the repo contains credentials or the bucket name. `urbanbio config
check` fails if a required variable is missing, if `URBANBIO_SITES` doesn't parse or lacks a
site referenced by a deployment, or if any public config value looks like a coordinate pair at
a precision finer than `coordinate_precision_deg`.

### Loading and the private boundary

`config.load()` returns a `PipelineConfig` (public fields plus `sites: list[Site]`). Private
`SiteSecret` objects (with exact coordinates) are used inside `config.load()` only to compute
each Site's generalized coordinates, then discarded. `PipelineConfig` has no attribute through
which exact coordinates can be reached. `SiteSecret.__repr__` masks coordinates so a traceback
can't print them.

Exact coordinates exist in two kinds of places, both private: the `URBANBIO_SITES` secret,
and the positions recorders write into their own file headers and logs, which survive only in
the private archive's sidecars and device logs
([archive](../archive/archive-design.md)). They never appear in tables, run records, or logs.

### Codespace lifecycle

The runbook covers two Codespaces settings that protect pipeline work:

- **Idle timeout** at its maximum (240 minutes) before long runs
  ([runs](../runs/runs-design.md) § Long Runs in a Codespace).
- **Retention.** GitHub deletes a Codespace stopped for longer than its retention period
  (30 days at most). Deletion loses the quarantine and work directories but nothing
  authoritative: configuration and credentials are secrets, results are in AWS S3, and a card
  isn't wiped until its files are archived
  ([intake](../ingest/intake/intake-design.md) § Card Wipe Readiness). The maintainer opens the
  Codespace at least monthly.

## CLI

Typer application `urbanbio`, installed as a console script.

| Command | Purpose | Run stage |
|---|---|---|
| `urbanbio config check` | Validate config, environment, and privacy guards | — |
| `urbanbio site list` | Show sites with generalized coordinates | — |
| `urbanbio deployment create --site S --serial N --recorder-name R --start T [--adapter A]` | Register a deployment; refuses to overlap an active deployment on the same device | `admin` |
| `urbanbio deployment end DEPLOYMENT --end T` | Close a deployment | `admin` |
| `urbanbio ingest PATH --deployment D --retrieved T [--clock-offset-s X] [--retrieval R]` | Hash, parse, and register a card copied into the quarantine | `ingest` |
| `urbanbio screen --retrieval R` | Run both speech detectors; write speech events | `screen` |
| `urbanbio archive --retrieval R [--no-purge]` | Mask, encode FLAC, upload, verify; purge each verified original unless held or `--no-purge` | `archive` |
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

Exit codes: `0` success; `1` run failed; `2` run partial (some units flagged or failed);
`3` configuration or environment error; `4` lock held.

## Errors

```
UrbanbioError
├── ConfigError            # bad or missing configuration, missing secrets
├── PrivacyError           # a guard tripped (unmasked audio outside quarantine, private key in params)
├── IntegrityError         # checksum or bit-identity mismatch
├── AdapterError           # unparseable or inconsistent device data
├── TimeNormalizationError # missing or contradictory time-zone information
├── StorageError           # AWS S3 failure after retries
└── LockError
```

Every error carries a `reason_code` (a short constant such as `checksum_mismatch`,
`guano_missing_timestamp`) used in the ledger and run record.

- **Unit-level check failures** (`IntegrityError`, `AdapterError`, `TimeNormalizationError`)
  flag that unit for a human decision, record the reason, and the run continues with other
  units (Tenet 3). The run ends `partial`.
- **Unit-level transient failures** (a model or detector raising, a file that fails to decode,
  an AWS S3 error after retries on one object) mark that unit `failed`. The next run retries it
  ([runs](../runs/runs-design.md) § Stage-Unit Ledger). The run ends `partial`.
- **Run-level errors** (`ConfigError`, `PrivacyError`, `LockError`, and `StorageError` after
  retries) stop the run immediately with status `failed`.
- **AWS S3 retries** use botocore's `standard` retry mode with 5 attempts.
- A `PrivacyError` is never caught by stage code.

## Logging

Standard library `logging`:

- Console: human-readable, `INFO` by default, `--verbose` for `DEBUG`.
- File: JSON lines at `/workspaces/state/logs/<run_id>.log` with `ts_utc`, `level`, `run_id`,
  `stage`, `unit_id`, `event`, `message`, plus structured fields. Uploaded to
  `runs/<run_id>.log` when the run ends.
- Logs contain IDs, offsets, counts, and checksums, never audio samples, exact coordinates,
  credentials, or the bucket name. A logging filter redacts values of `URBANBIO_BUCKET`,
  `URBANBIO_SITES`, and `AWS_*` environment variables if they appear in a message.

## Dependencies

Pinned in `pyproject.toml`, locked in `uv.lock`. Python 3.13 (the newest version with LiteRT
wheels).

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
  `/workspaces/.cache/test-audio`. These tests carry `@pytest.mark.models` and are opt-in
  (`uv run pytest -m models`), not run in CI by default.
- **AWS S3** is mocked with `moto` in unit and integration tests. No test touches a real
  bucket.
- **Real-card checks** (DST behavior, actual header layout) are verified by the maintainer
  against the first real cards using `urbanbio` commands. Findings are written into the
  [songmeter-micro2](../ingest/songmeter-micro2/songmeter-micro2-design.md) LLD and encoded as
  synthetic fixtures, never as copies of real files.
- Every test cites the EARS IDs it verifies with `# @spec` comments.

## Decisions & Alternatives

| Decision | Chosen | Alternatives Considered | Rationale |
|---|---|---|---|
| Config format | TOML + Pydantic | YAML; environment variables only | `tomllib` is in the standard library; Pydantic gives typed validation. Secrets alone come from the environment (HLD §8). |
| Bucket name | Environment variable (Codespaces secret) | Private config file | The bucket name isn't committed and survives Codespace rebuilds with the other secrets. |
| Exact coordinates | TOML in a Codespaces secret, reduced at load time | Private file in the Codespace with an offline backup; copy in the private AWS S3 bucket with backup/restore commands | The secret survives Codespace deletion with no backup step to forget, follows the rule that secrets come only from the environment, and leaves no private file on disk to commit by accident. Editing is less convenient, but sites change rarely. |
| CLI framework | Typer | argparse; Click | Type-hinted commands with little code. Click is its dependency anyway. |
| Real-model tests | Opt-in, public-domain audio downloaded at test time | Commit small public-domain clips; mocks only | Keeps the repository audio-free (CI guard stays absolute) while still exercising real models. |
| torch build | CPU-only index | Default PyPI wheel | The default Linux wheel pulls CUDA libraries (several GB) onto a 32 GB Codespace disk. |

## Open Questions & Future Decisions

### Deferred
1. Whether a second environment (e.g., a larger machine for reprocessing) needs its own public
   config file.

## References

- HLD §2, §6.2, §8
- Typer: https://typer.tiangolo.com/
- uv PyTorch integration: https://docs.astral.sh/uv/guides/integration/pytorch/
- moto: https://docs.getmoto.org/
