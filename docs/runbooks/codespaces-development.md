# Runbook: Codespaces Development (example deployment)

This is the maintainer's development setup: a GitHub Codespace built from the repository's dev
container, running the `dev` profile with a `filesystem` store. It is one example of a dev
profile host; any host where the installer runs with `--profile dev`, or any dev container
host, works the same way. Design: [install](../intent/install/install-design.md) § Dev
Container and [config-cli](../intent/config-cli/config-cli-design.md) § The `dev` profile.

The Codespace never holds real field audio, real site coordinates, or the production store's
credentials. The `dev` profile enforces the last two: it refuses `URBANBIO_SITES`, and it
refuses a store whose marker names `processing`.

## 1. Create the Codespace

Create a Codespace on `main` from the repository page. The dev container sets
`URBANBIO_PROFILE=dev` and installs the system packages, the pinned uv, and all dependency
groups.

## 2. Keep dev data across rebuilds

The `dev` profile's default paths are under `~`, which a Codespace rebuild discards. Files under
`/workspaces` survive rebuilds, so this deployment overrides the paths with an operator config
file there, outside the repository working tree:

```bash
mkdir -p /workspaces/.urbanbio
cat > /workspaces/.urbanbio/dev.toml <<'TOML'
[paths]
quarantine = "/workspaces/dev-data/quarantine"
work = "/workspaces/dev-data/work"
state = "/workspaces/dev-data/state"
model_cache = "/workspaces/.cache/birdnet"
test_audio_cache = "/workspaces/.cache/test-audio"

[store]
backend = "filesystem"
root = "/workspaces/dev-data/store"
TOML
```

Set `URBANBIO_CONFIG=/workspaces/.urbanbio/dev.toml` as a user-level Codespaces secret scoped to
this repository (the value isn't secret; a Codespaces secret is the way to set a variable in
every Codespace), then restart the Codespace so it sees the variable.

## 3. Initialize directories and the dev store

```bash
bash scripts/install.sh --profile dev     # Creates the directories with mode 0700; idempotent
mkdir -p /workspaces/dev-data/store
uv run urbanbio store init
uv run urbanbio config check
```

## 4. Remove production values from Codespaces

The `dev` profile needs no private values. If user-level Codespaces secrets named
`URBANBIO_SITES`, `URBANBIO_BUCKET`, `AWS_ACCESS_KEY_ID`, or `AWS_SECRET_ACCESS_KEY` exist for
this repository, delete them: `config check` fails while `URBANBIO_SITES` is set, and the others
give a dev host access to production data it never needs.

```bash
gh secret list --user --app codespaces
gh secret delete URBANBIO_SITES --user --app codespaces
```

## Losing the Codespace

GitHub deletes a Codespace that stays stopped longer than its retention period. That loses only
synthetic data and caches. Recreate it and repeat steps 2–3.
