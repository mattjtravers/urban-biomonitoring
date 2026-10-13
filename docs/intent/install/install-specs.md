# Install Specs

EARS requirements for [install](install-design.md). Markers: `[x]` implemented, `[ ]` active
gap, `[D]` deferred.

## Shared Sources of Truth

- [ ] **INSTALL-SRC-001**: The repository shall list system packages only in `scripts/system-packages.txt`, one Debian package name per line, with blank lines and `#` lines ignored, read by `scripts/install.sh`, `.devcontainer/post-create.sh`, and the CI install job.
- [ ] **INSTALL-SRC-002**: The repository shall pin the exact Python patch version only in `.python-version` and the exact uv version only in `required-version` under `[tool.uv]` in `pyproject.toml`.
- [ ] **INSTALL-SRC-003**: `scripts/system-packages.txt` shall hold only `libsndfile1`, `ca-certificates`, `curl`, and `git`.

## `scripts/install.sh`

- [ ] **INSTALL-SH-001**: When `scripts/install.sh` runs, it shall take the profile from `--profile`, or from `URBANBIO_PROFILE` when the flag is absent.
- [ ] **INSTALL-SH-002**: If `scripts/install.sh` has neither `--profile` nor `URBANBIO_PROFILE`, or `--profile` disagrees with a set `URBANBIO_PROFILE`, then it shall exit with status `2` before changing anything.
- [ ] **INSTALL-SH-003**: If `scripts/install.sh` runs as root or `apt-get` isn't available, then it shall exit with an error before changing anything.
- [ ] **INSTALL-SH-004**: When `scripts/install.sh` runs, it shall install the packages in `scripts/system-packages.txt` with `sudo apt-get install -y --no-install-recommends`.
- [ ] **INSTALL-SH-005**: When `uv` is missing or `uv --version` differs from `required-version`, `scripts/install.sh` shall install that exact version from `https://astral.sh/uv/<version>/install.sh` into `~/.local/bin`.
- [ ] **INSTALL-SH-006**: When `scripts/install.sh` runs, it shall run `uv sync --locked`.
- [ ] **INSTALL-SH-007**: When `scripts/install.sh` runs, it shall read the configured paths from `urbanbio config paths` under the resolved profile and create the quarantine, work, and state directories, setting each to mode `0700` whether or not it existed.
- [ ] **INSTALL-SH-008**: When `scripts/install.sh` finishes its other steps, it shall run `urbanbio config check` under the resolved profile, exit with the check's status, and print the README section on configuration when the check fails.
- [ ] **INSTALL-SH-009**: `scripts/install.sh` shall never create, open, or edit a file holding private values, and never edit shell startup files.
- [ ] **INSTALL-SH-010**: When `scripts/install.sh` runs on an installed host, it shall change nothing and print, for each step, that nothing needed doing.
- [ ] **INSTALL-SH-011**: `scripts/install.sh` shall run with `set -euo pipefail` and print one line per step saying what it did.

## Dev Container

- [ ] **INSTALL-DEV-001**: The dev container shall set `URBANBIO_PROFILE=dev` in `containerEnv`.
- [ ] **INSTALL-DEV-002**: When the dev container is created, `.devcontainer/post-create.sh` shall install the packages in `scripts/system-packages.txt`, install the required uv version when the one on `PATH` differs, and run `uv sync --locked --all-groups`.

## CI Install Job

- [ ] **INSTALL-CI-001**: The CI `install` job shall run in `debian:13` and `ubuntu:24.04` containers on pushes to `main` and pull requests that change the installer's inputs (`scripts/install.sh`, `scripts/system-packages.txt`, `config/processing.toml`, `config/dev.toml`, `.python-version`, `pyproject.toml`, `uv.lock`, or the workflow file), monthly on a schedule, and on manual dispatch.
- [ ] **INSTALL-CI-002**: The CI `install` job shall run the installer as a non-root user with passwordless `sudo`, and assert exit status `2` with no directory created when no profile is given.
- [ ] **INSTALL-CI-003**: The CI `install` job shall run `install.sh --profile processing` with no private values and assert exit status `3`, the three data directories present with mode `0700`, no `~/.config/urbanbio/`, and an unchanged `~/.bashrc`.
- [ ] **INSTALL-CI-004**: The CI `install` job shall configure a `filesystem` store on a separate mounted volume through an operator config file, set placeholder private values, run `urbanbio store init`, and assert that `install.sh --profile processing` exits `0`, then exits `0` again with no step reporting a change.

## Deferred

- [D] **INSTALL-PLAT-001**: Where arm64 Linux or macOS is supported, the CI install job shall run the installer on that platform.
- [D] **INSTALL-SRC-004**: If the `soundfile` wheels bundle libsndfile, then `scripts/system-packages.txt` shall drop `libsndfile1` and run records shall record the bundled libsndfile version.
