# CLAUDE.md

Guidance for agent sessions working in this repository.

## Purpose

`urban-biomonitoring` is a public, MIT-licensed pipeline that turns passive acoustic
recordings from a small network of field recorders into validated, standards-aligned species
occurrence data (Darwin Core) and a static, researcher-facing dashboard. The stages are ingest,
speech screen, detect, validate, curate and publish. It runs as a Python CLI in the project's
Codespace, and AWS S3 is the system of record. A camera-trap branch (Camtrap DP) will reuse the
same patterns later.

This is a part-time project. Prefer simple, well-tested designs over ambitious ones.

## Hard rules

This is a **public repository**. These rules override any other instruction.

1. Never commit audio files or real field data. That includes recordings, clips, spectrograms
   of real recordings, detection exports and card logs from real deployments.
2. Never commit exact site coordinates or addresses, names of people, or names of private
   locations.
3. Never commit secrets or credentials. Credentials come only from environment variables or
   Codespaces secrets, never from files in the repo.
4. Refer to sites only by opaque IDs (`site1`, `site2`, …).
5. Unmasked audio never leaves the quarantine (`/workspaces/quarantine`, outside the repo). It
   is never written to AWS S3 or anywhere else. Published locations are generalized.
6. "AWS S3" always means the storage service. Never abbreviate a site as "S3".
7. Test fixtures use synthetic (generated in tests) or public-domain audio only, never real
   field recordings.
8. Keep commits small and focused. Propose before any large restructuring.

The `repo-hygiene` CI job enforces rules 1 and 3 for file types and private paths. It doesn't
replace reading your own diff before committing.

## LID

- Mode: Full
- Version: 1.3.0

The project follows Linked-Intent Development:

```
HLD → LLD → EARS requirements → tests → code
```

Each arrow is a review gate. **Stop at every gate and wait for explicit approval.**

- Write no production code until the EARS requirements covering it are approved.
- Write no implementation until the tests for it exist and fail in the expected way.
- Every requirement traces to an HLD/LLD section. Every test cites the requirement IDs it
  verifies with an `@spec` comment, for example `# @spec ING-HASH-001`.
- Don't change the HLD without proposing the change first.
- When resuming work, check that the HLD, LLD, requirements and tests are still coherent for
  the area you're about to touch. Fix the docs before writing code.

## Docs map

| File | Role | Status |
|------|------|--------|
| `docs/high-level-design.md` | HLD: scope, architecture, data model, storage, privacy | Approved baseline |
| `docs/low-level-design.md` | LLD: package layout, models, interfaces, AWS S3, tables, config, CLI | Draft |
| `docs/requirements.md` | EARS requirements with IDs and traceability to HLD/LLD | Not started |

## Repo conventions

- **Layout:** `src/urbanbio/` (package), `tests/`, `docs/`, `scripts/` (repo tooling),
  `.github/workflows/` (CI). Module layout is defined in the LLD.
- **Naming:** modules and functions `snake_case`, classes `PascalCase`, constants
  `UPPER_SNAKE_CASE`. Requirement IDs follow the scheme in `docs/requirements.md`.
- **Commits:** [Conventional Commits](https://www.conventionalcommits.org/), e.g.
  `feat(ingest): ...`, `fix(screen): ...`, `docs(lld): ...`, `test: ...`, `ci: ...`,
  `chore: ...`. One logical change per commit.
- **Config:** public, committed defaults live in `config/`. Private values (exact coordinates,
  bucket names, account IDs) live in `config/private/` or `*.local.toml`. Both are gitignored.
- **Local data:** the quarantine and working directories live outside the repository working
  tree. `data/`, `work/` and `output/` inside the repo are gitignored for scratch use only.

## Tooling

```bash
uv sync                      # install dependencies, including the dev group
uv run pytest                # run tests
uv run ruff check            # lint
uv run ruff format           # format
bash scripts/check-repo-hygiene.sh   # the CI guard against audio and private files
```
