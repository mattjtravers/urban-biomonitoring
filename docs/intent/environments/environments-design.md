---
parent: high-level-design
prefix: ENV
---

# Environments and Processing-Machine Setup

## Context and Design Philosophy

The pipeline runs in two environments (HLD §2): the **processing machine**, a local Linux
machine that handles all real media, and the **development environment**, the project's
Codespaces dev container. This LLD covers how each is provisioned, how they are kept in step,
and how the processing machine is rebuilt from the repository alone (HLD §8 "Reproducible
processing machine"). Configuration and private values are covered in
[config-cli](../config-cli/config-cli-design.md).

Three principles drive it:

- **The repository is the machine's description.** Everything a script can do is in
  `scripts/setup-local.sh`. Everything it can't do is in
  `docs/runbooks/local-processing-setup.md`. Nothing about the processing machine lives only in
  the maintainer's memory.
- **One source per fact.** System packages, the Python version, and the uv version are each
  written down once and read by both environments and by CI, so the environments can't drift.
- **An untested setup script is a broken setup script.** CI runs the script in a clean Debian
  container whenever its inputs change, and on a schedule, because apt and the uv installer
  change underneath a script that doesn't.

## Shared Sources of Truth

| Fact | Single source | Read by |
|---|---|---|
| System packages | `scripts/system-packages.txt` | `scripts/setup-local.sh`, `.devcontainer/post-create.sh`, CI setup job |
| Python version | `.python-version` (exact patch, e.g. `3.13.16`) | uv in every environment |
| uv version | `required-version` under `[tool.uv]` in `pyproject.toml` (exact, e.g. `==0.12.23`) | `setup-local.sh` (installs it); `astral-sh/setup-uv` in CI (reads it); uv itself (refuses to run on a mismatch) |
| Python dependencies | `uv.lock` | `uv sync --locked` in every environment |

`scripts/system-packages.txt` holds one Debian package name per line. Blank lines and lines
starting with `#` are ignored. Versions aren't pinned: both environments track their Debian
release's stable packages. The list holds the audio libraries the pipeline needs (`ffmpeg`,
`libsndfile1`, `sox`) the tools the setup script needs (`ca-certificates`, `curl`, `git`), and
`tmux` for long runs ([runs](../runs/runs-design.md) § Long Runs on the Processing Machine).

The dev container's uv feature installs whatever version it ships. When that differs from
`required-version`, uv refuses to run, so drift shows up as an error in the dev container
instead of a silent difference. `post-create.sh` then installs the required version.

## Development Environment

`.devcontainer/devcontainer.json` sets `URBANBIO_ENV=dev` in `containerEnv`.
`.devcontainer/post-create.sh` installs the packages in `scripts/system-packages.txt`, installs
the required uv version if the one on `PATH` differs, and runs `uv sync --locked --all-groups`.
Nothing about the processing machine goes into the dev container.

## Processing Machine

### Reference machine

The current processing machine is recorded in the runbook with its confirmed specifications:
a ChromeOS Linux environment (Crostini, Debian 13 "trixie") on x86_64 with 8 cores, 6.4 GB of RAM available
to Linux, no swap, and a 50 GB Linux disk. Disk budgets ([intake](../ingest/intake/intake-design.md)
§ Disk Budget) and worker defaults ([runs](../runs/runs-design.md) § Resource Limits) are sized
for it. Moving to a different machine means updating the runbook's specifications and
re-checking those two sections.

### `scripts/setup-local.sh`

Run from a clone of the repository as the maintainer's own user:
`bash scripts/setup-local.sh`. The script is idempotent: a second run on a set-up machine
changes nothing and ends in the same state. Steps, in order:

1. **Preconditions.** Refuse to run as root (it calls `sudo` itself), refuse to run in a
   Codespace (`CODESPACES=true`), and refuse when `apt-get` is not available.
2. **System packages.** `sudo apt-get install -y --no-install-recommends` with the packages
   in `scripts/system-packages.txt`.
3. **uv.** Read `required-version` from `pyproject.toml`. If `uv` is missing, or
   `uv --version` differs, install that exact version with the official installer from
   `https://astral.sh/uv/<version>/install.sh` into `~/.local/bin`.
4. **Python dependencies.** `uv sync --locked`. uv installs the Python version named in
   `.python-version`.
5. **Directories.** Ask the CLI for the configured paths (`uv run urbanbio config paths`,
   with `URBANBIO_ENV=processing`), create the quarantine, work, and state directories, and set
   each one to mode `0700`. Creating an existing directory is a no-op, but its mode is always
   set to `0700`.
6. **Secrets file.** Create `~/.config/urbanbio/` with mode `0700`. If
   `~/.config/urbanbio/env` doesn't exist, copy `config/urbanbio.env.template` to it with mode
   `0600`. An existing file is never opened for writing, replaced, or re-moded. The template
   contains `URBANBIO_ENV=processing` and the private variable names with empty values, and
   never any value.
7. **Shell loading.** If `~/.bashrc` doesn't already contain the loader line, append it:
   `set -a; [ -f ~/.config/urbanbio/env ] && . ~/.config/urbanbio/env; set +a`. The line is
   matched exactly, so it is added at most once.
8. **Check.** Load the env file into the script's own environment and run
   `uv run urbanbio config check`. The script's exit status is the check's. On a fresh machine
   the check fails because the secrets are empty, and the script prints the runbook section that
   fills them in.

Steps 1–4 fail fast (`set -euo pipefail`). Each step prints one line saying what it did or
that nothing needed doing.

Because steps 5 and 8 call the CLI, the setup script depends on the configuration module and
`config` commands ([config-cli](../config-cli/config-cli-design.md)) being implemented first.

### Runbook: `docs/runbooks/local-processing-setup.md`

Covers what a script can't do: enabling Linux on ChromeOS, setting the Linux disk size,
sharing the SD card reader with Linux, power and sleep settings, cloning the repository,
running the setup script, filling in the secrets file from the maintainer's password manager,
and re-running the script until `config check` passes. It also records the reference machine's
specifications.

### Each processing run starts from tested code

The weekly card runbook starts with `git pull && uv sync --locked`, so the processing machine
runs the code and locked dependencies that CI tested on `main`.

## CI: setup-local job

A `setup-local` job in `.github/workflows/ci.yml` runs the script in a `debian:trixie`
container, matching the reference machine's Debian release (Debian 13, recorded in the
runbook). When the processing machine moves to a new Debian release, the job's image moves with
it. It runs:

- on pushes to `main` and on pull requests that change `scripts/setup-local.sh`,
  `scripts/system-packages.txt`, `config/urbanbio.env.template`, `config/processing.toml`,
  `.python-version`, `pyproject.toml`, `uv.lock`, or the workflow file;
- monthly on a schedule;
- on manual dispatch.

The job:

1. Installs `sudo`, creates a non-root user with passwordless `sudo`, and checks out the
   repository as that user.
2. Runs `setup-local.sh` and expects exit status `3`: everything succeeded except `config
   check`, which fails on the empty secrets.
3. Asserts that the env file has mode `0600`, its directory `0700`, and the three data
   directories `0700`, that the env file holds no values, and that `~/.bashrc` has the loader
   line once.
4. Fills the env file with obviously fake values (a fake bucket name and keys, and a
   synthetic site with placeholder coordinates), records its checksum, and runs the script
   again. It expects exit status `0`, the env file's checksum unchanged, and the loader line
   still present exactly once.

No real secret is available to this job, and `config check` makes no AWS call, so the job
needs none.

## Decisions & Alternatives

| Decision | Chosen | Alternatives Considered | Rationale |
|---|---|---|---|
| Processing machine provisioning | Idempotent shell script plus runbook | Docker image; Ansible; runbook only | Docker adds a daemon and a layer between the pipeline and the card reader on a 6.4 GB machine. Ansible is heavy for one machine. A runbook alone drifts silently. A shell script is the smallest thing CI can run. |
| Package list format | Plain text, one name per line | Inline lists in each script; a Brewfile-style manifest | Readable by `bash` and by CI with no parser, and impossible to keep two copies of. |
| uv pin source | `required-version` in `pyproject.toml` | A separate `.uv-version` file; pin only in CI | uv enforces `required-version` itself, so a mismatch anywhere is an error, and `setup-uv` reads it. |
| Python pin precision | Exact patch in `.python-version` | Minor version only | The processing machine and CI run the same interpreter build that the lockfile was tested with. |
| Existing secrets file | Never written, replaced, or re-moded | Fix the mode on every run; merge new variable names in | The file is the only local copy of private values. A script that writes to it can destroy them. A wrong mode is reported by `config check`, and a new variable name is a runbook step. |
| CI coverage | On changes to the script's inputs, monthly, and on dispatch | Every push; only on changes | Running on every push costs minutes for no signal. Running only on changes misses breakage from apt or the uv installer while the script is untouched. |

## Open Questions & Future Decisions

### Deferred
1. Whether to add swap inside Crostini if a first-card measurement shows memory pressure
   ([runs](../runs/runs-design.md) § Resource Limits).

## References

- HLD §2, §8
- [config-cli](../config-cli/config-cli-design.md): environments, config files, private values
- `docs/runbooks/local-processing-setup.md`
- uv `required-version`: https://docs.astral.sh/uv/reference/settings/#required-version
- uv installer: https://docs.astral.sh/uv/getting-started/installation/
- `astral-sh/setup-uv`: https://github.com/astral-sh/setup-uv
