# urban-biomonitoring
An open-source pipeline that turns passive acoustic recordings from a small network of field recorders into validated, standards-aligned species occurrence data and a public, researcher-facing dashboard.

The pipeline is a Python CLI (`urbanbio`) that you run on your own host against your own
recorder media. Design: [high-level design](docs/high-level-design.md) and
[`docs/intent/`](docs/intent/).

## Prerequisites

- **Operating system:** Linux on x86_64. Tested on Debian 13 and Ubuntu 24.04 LTS; other
  distributions are best-effort. The installer needs `apt-get`.
- **Python and uv:** the versions pinned in `.python-version` and in `required-version` in
  `pyproject.toml`. The installer installs both.
- **System packages:** those listed in `scripts/system-packages.txt`. The installer installs
  them.
- **A store** for archived audio, tables, and run records. The default is an AWS S3 bucket, which
  needs an AWS account and an access key for the pipeline. You can use a directory on a mounted
  filesystem instead, such as a lab share
  ([docs/runbooks/filesystem-store.md](docs/runbooks/filesystem-store.md)).
- **Host sizing,** per recorder-week of audio (48 kHz, 16-bit mono, 1 minute on / 4 off):

  | Resource | Guidance |
  |---|---|
  | Working disk | about 12 GB per recorder-week held at once, plus about 10 GB for dependencies, models, and free-space margin. The defaults (a 35 GB quarantine budget) assume about 50 GB free. |
  | Memory | at least 8 GB of RAM for the default of 2 workers |
  | Store | about 7 GB per recorder-week, so about 360 GB per recorder-year |

  Details: [install LLD § Sizing guidance](docs/intent/install/install-design.md#sizing-guidance).

## Install

```bash
git clone https://github.com/mattjtravers/urban-biomonitoring.git
cd urban-biomonitoring
bash scripts/install.sh --profile processing     # or --profile dev
```

The installer is idempotent. It installs system packages, uv, and locked dependencies, creates
the configured data directories with mode `0700`, and ends with `urbanbio config check`. That
check fails until the steps below are done.

## Configure

1. **Choose a profile** by setting `URBANBIO_PROFILE`:
   - `processing` handles real recorder media;
   - `dev` runs on synthetic sites and test audio only, and refuses production data.
2. **Override defaults if needed.** Paths, worker count, disk budget, and the store backend have
   documented defaults in `config/<profile>.toml`. To change any of them without editing
   committed files, write an operator config file outside the repository and set
   `URBANBIO_CONFIG` to its path.
3. **Provide private values** as environment variables. How you set them (an env file loaded by
   your shell, a secrets manager, a hosted environment's secret store) is up to you; the
   software only reads the environment and never prints them.

   | Variable | Needed for |
   |---|---|
   | `URBANBIO_SITES` | `processing`: exact site coordinates, as TOML (refused in `dev`) |
   | `URBANBIO_BUCKET`, `AWS_ACCESS_KEY_ID`, `AWS_SECRET_ACCESS_KEY` | the `s3` store backend |

4. **Initialize the store** once, when it's new: `uv run urbanbio store init`.
5. **Check:** `uv run urbanbio config check`.

## Runbooks

Concrete procedures, written for the maintainer's deployment as a worked example:

- [ChromeOS processing host](docs/runbooks/chromeos-processing-host.md)
- [Codespaces development](docs/runbooks/codespaces-development.md)
- [Weekly card processing](docs/runbooks/weekly-card.md)
- [Using a `filesystem` store](docs/runbooks/filesystem-store.md)

## License

MIT. BirdNET models carry their own license (CC BY-NC-SA 4.0) and are downloaded at runtime,
never included in this repository.
