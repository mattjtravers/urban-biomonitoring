---
parent: high-level-design
prefix: INSTALL
---

# Prerequisites and Installation

## Context and Design Philosophy

`urban-biomonitoring` is installed by whoever operates it, on a host of their choosing, against
their own recorder media and store (HLD §2, §8). This LLD covers what a host needs, how the
pipeline is installed on it, how the versions it runs are pinned, and how CI keeps the installer
working. Configuration profiles, the operator config file, and private values are covered in
[config-cli](../config-cli/config-cli-design.md); store backends in
[store](../store/store-design.md).

Three principles drive it:

- **The repository is the install description.** Everything a script can do is in
  `scripts/install.sh`. Everything specific to one operator's hosts (enabling a Linux
  environment, sharing a card reader, delivering secrets) is in runbooks, as example
  deployments.
- **One source per fact.** System packages, the Python version, and the uv version are each
  written down once and read by the installer, the dev container, and CI, so no two places can
  drift.
- **An untested installer is a broken installer.** CI runs it on standard Debian and Ubuntu
  images whenever its inputs change, and on a schedule, because apt and the uv installer change
  underneath a script that doesn't.

## Prerequisites

| Requirement | Value |
|---|---|
| Operating system | Linux on x86_64. Tested on Debian 13 and Ubuntu 24.04 LTS; other distributions are best-effort, and the installer requires `apt-get`. |
| Python | The exact version in `.python-version`, installed by uv |
| uv | The exact version in `required-version` under `[tool.uv]` in `pyproject.toml` |
| System packages | Those listed in `scripts/system-packages.txt` |
| Store | By default, an AWS account with an S3 bucket and an access key for the pipeline ([store](../store/store-design.md) § `s3` Backend). Alternatively, a directory on a mounted filesystem ([store](../store/store-design.md) § `filesystem` Backend). |
| Network | Outbound HTTPS for the installer, the BirdNET model download, and the `s3` backend |

### Sizing guidance

Audio volume, at the recording defaults in `config/pipeline.toml` (48 kHz, 16-bit mono,
288 recorded minutes per day; [intake](../ingest/intake/intake-design.md) § Disk Budget):

| Per recorder-week | Size |
|---|---|
| WAV from the card (working disk) | 11.6 GB |
| Masked FLAC in the store | about 7 GB (estimate until measured on a real card) |

A **processing host** needs:

- **Working disk:** about 12 GB per recorder-week held in the quarantine at once, plus about
  7 GB for Python dependencies, the uv cache, and models, plus `quarantine.min_free_gb`
  (default 3 GB). The default `quarantine.budget_gb` of 35 GB holds two recorder-weeks, so the
  defaults assume at least 50 GB of free disk.
- **Memory:** at least 8 GB of RAM for the default of 2 workers. Each audio stage holds one file
  per worker ([runs](../runs/runs-design.md) § Resource Limits). **TODO: measure on the first
  real card**: peak memory per worker, which turns this into a per-worker figure.
- **CPU:** any x86_64 CPU with at least as many cores as `resources.workers`.

A **store** grows by about 7 GB per recorder-week, so about 360 GB per recorder-year.

A **dev profile host** needs about 10 GB of free disk and 4 GB of RAM. It processes only
synthetic audio.

## Shared Sources of Truth

| Fact | Single source | Read by |
|---|---|---|
| System packages | `scripts/system-packages.txt` | `scripts/install.sh`, `.devcontainer/post-create.sh`, CI install job |
| Python version | `.python-version` (exact patch, e.g. `3.13.16`) | uv in every install |
| uv version | `required-version` under `[tool.uv]` in `pyproject.toml` (exact, e.g. `==0.12.23`) | `install.sh` (installs it); `astral-sh/setup-uv` in CI (reads it); uv itself (refuses to run on a mismatch) |
| Python dependencies | `uv.lock` | `uv sync --locked` in every install |

`scripts/system-packages.txt` holds one Debian package name per line. Blank lines and lines
starting with `#` are ignored. Versions aren't pinned: each host tracks its release's stable
packages. The list holds only what the pipeline and the installer need: the audio libraries
(`ffmpeg`, `libsndfile1`, `sox`) and the installer's tools (`ca-certificates`, `curl`, `git`).
Operator conveniences, such as a terminal multiplexer for long runs, are runbook steps.

## `scripts/install.sh`

Run from a clone of the repository as the operator's own user:
`bash scripts/install.sh --profile PROFILE`. The profile comes from `--profile`, or, when the
flag is absent, from `URBANBIO_PROFILE`. With neither, the script exits with status `2` before
doing anything, so it never creates one profile's directories on a host meant for the other. A
`--profile` that disagrees with a set `URBANBIO_PROFILE` is also refused with status `2`. The
script is idempotent: a second run on an installed host changes nothing and ends in the same state.
Steps, in order:

1. **Preconditions.** Resolve the profile (above). Refuse to run as root (it calls `sudo` itself), and refuse when
   `apt-get` is not available. It makes no other check of the host.
2. **System packages.** `sudo apt-get install -y --no-install-recommends` with the packages
   in `scripts/system-packages.txt`.
3. **uv.** Read `required-version` from `pyproject.toml`. If `uv` is missing, or
   `uv --version` differs, install that exact version with the official installer from
   `https://astral.sh/uv/<version>/install.sh` into `~/.local/bin`.
4. **Python dependencies.** `uv sync --locked`. uv installs the Python version named in
   `.python-version`.
5. **Directories.** Ask the CLI for the configured paths
   (`URBANBIO_PROFILE=<PROFILE> uv run urbanbio config paths`), which include any operator
   overrides from `URBANBIO_CONFIG`. Create the quarantine, work, and state directories and set
   each one to mode `0700`. Creating an existing directory is a no-op, but its mode is always
   set to `0700`.
6. **Check.** Run `URBANBIO_PROFILE=<PROFILE> uv run urbanbio config check`. The script's exit
   status is the check's. When the check fails, for example because private values aren't yet
   in the environment or the store hasn't been initialized, the script prints the README
   section that covers configuration.

The script never creates, opens, or edits a file that holds private values, and never edits
shell startup files. How private values reach the environment is the operator's choice
(HLD §8).

Steps 1–4 fail fast (`set -euo pipefail`). Each step prints one line saying what it did or
that nothing needed doing.

Because steps 5 and 6 call the CLI, the installer depends on the configuration module and
`config` commands ([config-cli](../config-cli/config-cli-design.md)) being implemented first.

## Dev Container

`.devcontainer/` is an optional convenience for development. It works on any dev container
host. `devcontainer.json` sets `URBANBIO_PROFILE=dev` in `containerEnv`. `post-create.sh`
installs the packages in `scripts/system-packages.txt`, installs the required uv version if the
one on `PATH` differs, and runs `uv sync --locked --all-groups`. It also installs the development
tooling the maintainers use with this repository (the Claude Code feature and the LID plugins).
That tooling doesn't affect the pipeline, and an operator who doesn't use the dev container
doesn't need it.

The dev container's uv feature installs whatever version it ships. When that differs from
`required-version`, uv refuses to run, so drift shows up as an error instead of a silent
difference, and `post-create.sh` then installs the required version.

## Each Processing Run Starts from Tested Code

Commands that create runs under the `processing` profile refuse to start with uncommitted
changes ([runs](../runs/runs-design.md) § Run Record). Updating with
`git pull && uv sync --locked` before processing gives the code and locked dependencies that CI
tested on `main`; the weekly card runbook starts with that step.

## CI: install job

An `install` job in `.github/workflows/ci.yml` runs the installer in a matrix of `debian:13`
and `ubuntu:24.04` containers. It runs:

- on pushes to `main` and on pull requests that change `scripts/install.sh`,
  `scripts/system-packages.txt`, `config/processing.toml`, `config/dev.toml`,
  `.python-version`, `pyproject.toml`, `uv.lock`, or the workflow file;
- monthly on a schedule;
- on manual dispatch.

For each image, the job:

1. Installs `sudo`, creates a non-root user with passwordless `sudo`, and checks out the
   repository as that user.
2. Runs `install.sh` with no arguments and no `URBANBIO_PROFILE`, and expects exit status `2`
   and no directory created.
3. Runs `install.sh --profile processing` with no private values in the environment and expects
   exit status `3`: every step succeeded except `config check`.
4. Asserts that the quarantine, work, and state directories exist with mode `0700`, that
   `~/.config/urbanbio/` doesn't exist, and that `~/.bashrc` is byte-for-byte unchanged.
5. Writes an operator config file in a temporary directory that selects the `filesystem`
   backend with its root on a volume mounted into the job container (a separate filesystem from
   the quarantine, as the same-filesystem rule requires), sets `URBANBIO_CONFIG` to it, and sets obviously fake private
   values (a synthetic site with placeholder coordinates). Runs `urbanbio store init`, then
   `install.sh --profile processing` again, and expects exit status `0`.
6. Runs `install.sh --profile processing` a third time and expects exit status `0` and no step reporting a change.

No real secret is available to this job, and neither `config check` nor the `filesystem` backend
makes a network call to a store, so the job needs none.

## Decisions & Alternatives

| Decision | Chosen | Alternatives Considered | Rationale |
|---|---|---|---|
| Provisioning | Idempotent shell script plus runbooks | Docker image; Ansible; runbooks only | A container adds a daemon and a layer between the pipeline and the card reader, and complicates local-disk quarantine. Ansible is heavy for one host. Runbooks alone drift silently. A shell script is the smallest thing CI can run. |
| Installer scope | Packages, uv, dependencies, data directories, check | Also create an env file and add a loader to `~/.bashrc` | How private values reach the environment differs by operator (env file, secrets manager, hosted secret store). An installer that writes a secrets file chooses for them and risks destroying the only local copy of the values. |
| Installer profile | Required, from `--profile` or `URBANBIO_PROFILE` | Default to `processing` | A default would create real-media directories on a dev host run without the flag. Asking costs one word. |
| Host checks in the installer | Only "not root" and "`apt-get` present" | Detect the distribution, a container, or a hosted development environment and refuse or adapt | Configuration over detection (HLD Tenet 5). The installer needs `apt-get` and nothing else about the host. |
| Supported OS | Linux x86_64; Debian 13 and Ubuntu 24.04 tested | Also macOS; also arm64 | The audio stack and LiteRT wheels are exercised on x86_64 Linux. Each further platform is another CI target to keep green. |
| Package list format | Plain text, one name per line | Inline lists in each script; a Brewfile-style manifest | Readable by `bash` and by CI with no parser, and impossible to keep two copies of. |
| uv pin source | `required-version` in `pyproject.toml` | A separate `.uv-version` file; pin only in CI | uv enforces `required-version` itself, so a mismatch anywhere is an error, and `setup-uv` reads it. |
| Python pin precision | Exact patch in `.python-version` | Minor version only | Every install and CI run the same interpreter build that the lockfile was tested with. |
| CI coverage | On changes to the installer's inputs, monthly, and on dispatch | Every push; only on changes | Running on every push costs minutes for no signal. Running only on changes misses breakage from apt or the uv installer while the script is untouched. |
| CI images | `debian:13` and `ubuntu:24.04` | The GitHub runner image only; one distribution | Standard images are what a new operator starts from. The runner image carries preinstalled tools that would hide a missing package. |

## Open Questions & Future Decisions

### Deferred
1. Support for arm64 Linux and for macOS, each with its own CI target.
2. A per-worker memory figure for the sizing guidance, from the first real card's run records.

## References

- HLD §2, §8, Tenet 5
- [config-cli](../config-cli/config-cli-design.md): profiles, config files, private values
- [store](../store/store-design.md): store backends and setup
- uv `required-version`: https://docs.astral.sh/uv/reference/settings/#required-version
- uv installer: https://docs.astral.sh/uv/getting-started/installation/
- `astral-sh/setup-uv`: https://github.com/astral-sh/setup-uv
