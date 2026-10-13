# Configuration and CLI Specs

EARS requirements for [config-cli](config-cli-design.md). Markers: `[x]` implemented, `[ ]`
active gap, `[D]` deferred.

## Package Layout

- [ ] **CLI-PKG-001**: The system shall organize `src/urbanbio/` into the packages listed in config-cli § Package Layout, with `models`, `storage`, `provenance`, and `config` shared by every stage.
- [ ] **CLI-PKG-002**: No stage package (`ingest`, `screen`, `detect`, `validate`, `curate`, `publish`) shall import another stage package, and `storage.archive` importing `screen.mask` shall be the only import from a stage package into another package.
- [ ] **CLI-PKG-003**: The system shall expose each stage through one entry point function that the CLI calls and that tests can call without the CLI.

## Profiles and Layers

- [ ] **CLI-PROF-001**: If `URBANBIO_PROFILE` is unset or names a profile other than `processing` or `dev`, then every command except `--help` shall exit with a `ConfigError`.
- [ ] **CLI-PROF-002**: The system shall select the profile only from `URBANBIO_PROFILE`, never from properties of the host.
- [ ] **CLI-CFG-001**: When configuration loads, the system shall read `config/pipeline.toml`, then `config/<URBANBIO_PROFILE>.toml`, then the operator config file named by `URBANBIO_CONFIG` when set, with each key the operator file sets replacing the profile's value for that key.
- [ ] **CLI-CFG-002**: If `config/pipeline.toml` contains a deployment table (`[paths]`, `[resources]`, `[quarantine]`, `[store]`), or a profile file or the operator config file contains any other table, then the system shall raise a `ConfigError`.
- [ ] **CLI-CFG-003**: If `URBANBIO_CONFIG` is set to a path that is relative after `~` expansion, doesn't exist, isn't a regular file, or lies inside the repository working tree, then the system shall raise a `ConfigError`.
- [ ] **CLI-CFG-004**: The system shall validate the loaded configuration into Pydantic models, raising a `ConfigError` for an unknown key or a value of the wrong type.
- [ ] **CLI-CFG-005**: When configuration loads, the system shall expand `~` in and resolve every configured path, and shall raise a `ConfigError` for a path inside the repository working tree.

## Dev Profile

- [ ] **CLI-DEV-001**: While the profile is `dev`, the system shall load sites from `config/dev-sites.toml`.
- [ ] **CLI-DEV-002**: While the profile is `dev`, if `URBANBIO_SITES` is set, then every command shall exit with a `ConfigError` (`dev_private_sites`) before loading any site.
- [ ] **CLI-DEV-003**: While the profile is `dev`, `ingest`, `process`, `archive`, `purge`, and `rescreen` shall exit with a `ConfigError` before doing anything.

## Private Values

- [ ] **CLI-PRIV-001**: The system shall read private values (`URBANBIO_SITES`, `URBANBIO_BUCKET`, `AWS_ACCESS_KEY_ID`, `AWS_SECRET_ACCESS_KEY`) only from `os.environ`, never from a file.
- [ ] **CLI-PRIV-002**: While the profile is `processing`, the system shall parse `URBANBIO_SITES` as a TOML document of `[[site]]` tables with `site_id`, `latitude_exact`, `longitude_exact`, `timezone`, and optional `habitat` and `nws_station_id`.
- [ ] **CLI-PRIV-003**: The system shall hold exact site coordinates only in `SiteSecret` objects in a private registry inside `urbanbio.config`, and `PipelineConfig` shall have no attribute through which they can be reached.
- [ ] **CLI-PRIV-004**: When `config.site_offset_m(site_id, latitude_deg, longitude_deg)` is called, the system shall return the distance in meters from the site's exact position to the given point.
- [ ] **CLI-PRIV-005**: The system shall provide no function that returns a coordinate derived from exact coordinates other than the generalized coordinates computed at load.
- [ ] **CLI-PRIV-006**: The system shall mask coordinates in `SiteSecret.__repr__` and `__str__`.

## `config check`

- [ ] **CLI-CHECK-001**: When `urbanbio config check` runs, the system shall fail when `URBANBIO_PROFILE` is unset or unknown, the configuration layers break CLI-CFG-002, or `URBANBIO_CONFIG` breaks CLI-CFG-003.
- [ ] **CLI-CHECK-002**: When `urbanbio config check` runs, the system shall fail when a variable required by the profile and backend is missing or empty: `URBANBIO_SITES` in `processing`, and `URBANBIO_BUCKET`, `AWS_ACCESS_KEY_ID`, and `AWS_SECRET_ACCESS_KEY` with the `s3` backend.
- [ ] **CLI-CHECK-003**: When `urbanbio config check` runs, the system shall fail when the site configuration doesn't parse, names a time zone not in the IANA database, or lacks a site that a deployment in the `current_deployments` view references.
- [ ] **CLI-CHECK-004**: When `urbanbio config check` runs, the system shall fail when any table in a configuration layer holds a numeric key whose name contains `lat` and a numeric key whose name contains `lon` (case-insensitive), either of whose values isn't a multiple of `publish.coordinate_precision_deg` to within 10⁻⁹.
- [ ] **CLI-CHECK-005**: When `urbanbio config check` runs, the system shall fail when a configured path lies inside the repository working tree, or the quarantine, work, or state directory is missing or has a mode other than `0700`.
- [ ] **CLI-CHECK-006**: When `urbanbio config check` runs, the system shall fail when the store can't be opened (unreachable, marker missing, or marker naming another profile).
- [ ] **CLI-CHECK-007**: While the profile is `processing` with the `filesystem` backend, when `urbanbio config check` runs, the system shall fail when `store.root` is on the same filesystem as `paths.quarantine`.
- [ ] **CLI-CHECK-008**: While the profile is `dev`, when `urbanbio config check` runs, the system shall fail when `URBANBIO_SITES` is set.
- [ ] **CLI-CHECK-009**: When `urbanbio config check` finds failures, the system shall print each failing check with its fix, never print the value of a private variable, and exit `3`.
- [ ] **CLI-CHECK-010**: When `urbanbio config check` finds `resources.workers` greater than the host's reported CPU count, the system shall print a warning without failing.

## Commands

- [ ] **CLI-CMD-001**: The system shall install a Typer application `urbanbio` as a console script with the commands, arguments, and run stages listed in config-cli § CLI.
- [ ] **CLI-CMD-002**: When a command that processes units runs, the system shall load configuration, open the store, open a run, and call one stage entry point.
- [ ] **CLI-CMD-003**: When `urbanbio config paths` runs, the system shall print the configured paths for the current profile, expanded and resolved.
- [ ] **CLI-CMD-004**: When `urbanbio site list` runs, the system shall print each site with its generalized coordinates only.
- [ ] **CLI-CMD-005**: When `urbanbio deployment create` runs with `--settings FILE`, the system shall read any of `sample_rate_hz`, the `schedule` fields, `gain_db`, `mount_height_m`, `mount_heading_deg`, `tags`, and `comments` from that TOML file, taking unset recording values from `[recording]`, and shall raise a `ConfigError` for any other key or for a coordinate-like pair as defined in CLI-CHECK-004.
- [ ] **CLI-CMD-006**: When `urbanbio deployment create` or `deployment end` runs, the system shall record the change as an `admin` run with one unit.
- [ ] **CLI-CMD-007**: When `urbanbio process` runs, the system shall run `ingest`, `screen`, the optional speech sample (with `--speech-sample N`), `archive`, and `detect` in that order, each as its own run, passing a `partial` stage's succeeded files to the next stage, and when a stage's run ends `failed`, shall stop before the next stage and exit with that run's exit code.
- [ ] **CLI-CMD-008**: When `urbanbio query` runs, the system shall open a DuckDB shell on a connection from `urbanbio.storage.tables.connect` (DATA-VIEW-001).
- [ ] **CLI-CMD-009**: When `urbanbio runs list [--stage S]` or `runs show RUN` runs, the system shall print run records from the store.
- [ ] **CLI-EXIT-001**: The system shall exit `0` on success, `1` when a run failed, `2` when a run ended partial (some units flagged or failed), `3` on a configuration error (including the store marker), and `4` when the lock is held.

## Errors

- [ ] **CLI-ERR-001**: The system shall define the exception hierarchy `UrbanbioError` → `ConfigError`, `PrivacyError`, `IntegrityError`, `AdapterError`, `TimeNormalizationError`, `StorageError`, `LockError`, each carrying a `reason_code` used in the ledger and run record.
- [ ] **CLI-ERR-002**: When a unit raises `IntegrityError`, `AdapterError`, or `TimeNormalizationError`, the system shall flag the unit with the error's reason code and continue the run with other units.
- [ ] **CLI-ERR-003**: When a unit fails transiently (a model or detector raising, a file that fails to decode, a `StorageError` on the unit's own object after retries), the system shall mark the unit `failed` and continue the run with other units.
- [ ] **CLI-ERR-004**: If a `ConfigError`, `PrivacyError`, or `LockError` is raised, or a `StorageError` is raised on a run-level write (a batch's table or ledger part files, the run record, or the log), then the system shall stop the run immediately with status `failed`.
- [ ] **CLI-ERR-005**: If three consecutive units fail with `store_unavailable`, then the system shall stop the run as for a run-level `StorageError`.
- [ ] **CLI-ERR-006**: The system shall never catch `PrivacyError` in stage code.

## Logging

- [ ] **CLI-LOG-001**: The system shall log to the console in human-readable form at `INFO` by default and at `DEBUG` with `--verbose`.
- [ ] **CLI-LOG-002**: The system shall write a JSON-lines log at `<paths.state>/logs/<run_id>.log` with `ts_utc`, `level`, `run_id`, `stage`, `unit_id`, `event`, `message`, and structured fields.
- [ ] **CLI-LOG-003**: The system shall upload a run's log to `runs/<run_id>.log` after each batch's ledger write and when the run ends.
- [ ] **CLI-LOG-004**: When a command starts and finds a local run log newer than its copy in the store, the system shall upload that log before starting its own run.
- [ ] **CLI-LOG-005**: The system shall redact the values of `URBANBIO_BUCKET`, `URBANBIO_SITES`, and `AWS_*` environment variables from every log message, console and file.
- [ ] **CLI-LOG-006**: The system shall log store locations only as store-relative keys, and shall never log audio samples, exact coordinates, or credentials.

## Dependencies and Tests

- [ ] **CLI-DEP-001**: The project shall pin Python 3.13 in `.python-version`, pin dependencies in `pyproject.toml` and lock them in `uv.lock`, and install CPU-only `torch` from `https://download.pytorch.org/whl/cpu` through `[tool.uv.sources]`.
- [ ] **CLI-TEST-001**: The test suite shall generate test audio at test time into `tmp_path`, and the repository shall contain no audio files.
- [ ] **CLI-TEST-002**: When a test needs real-model audio, the system shall download public-domain audio from a pinned URL, verify it against a pinned SHA-256, and cache it in `paths.test_audio_cache`; such tests shall carry `@pytest.mark.models` and run only when selected with `-m models`.
- [ ] **CLI-TEST-003**: The test suite shall use only `filesystem` stores in `tmp_path` or `s3` stores mocked with `moto`, never a real bucket or share.

## Deferred

- [D] **CLI-PROF-003**: Where a `review` profile is defined, the system shall run clip review on a host other than the processing host with no `URBANBIO_SITES` and a read-only store key.
