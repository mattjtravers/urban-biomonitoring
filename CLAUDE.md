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

## Linked-Intent Development (MANDATORY)

**Consult the `linked-intent-dev` skill for ALL code changes.** All changes flow through the arrow of intent in one direction:

```
HLD → LLDs → EARS → Tests → Code
```

- **New features and refactors**: full six-phase workflow (HLD check → LLD check/draft → EARS → intent-narrowing edge audit → tests-first → code).
- **Bug fixes**: walk the arrow like any other change — find where behavior diverged from intent and cascade from there. No short-circuit.
- **If unsure**: use the full workflow.

Stop after each phase for user review. **Docs carry current intent, written to be read cold** — write each doc as if authored fresh today, from current intent alone: no narration of how it changed, no meaning that needs the conversation that produced it, no rebuttals to questions only a past discussion raised. Rationale, considered alternatives, and constraints a fresh author would independently write stay; record rejected alternatives and why in the LLD's Decisions & Alternatives table, not as asides in body prose.

**Memory vs. intent.** Before saving durable project knowledge to agent or tool memory, test whether it is project *intent* — would a fresh agent, in any tool, next session, need it to build this system correctly? If yes, record it in the arrow (HLD / LLD / EARS / decision doc), which travels and cascades — not in private, per-tool memory, where intent escapes the arrow. Knowledge about the user or how they like to work stays in memory.

Project-specific gates:

- Write no production code until the EARS specs covering it are approved, and no
  implementation until the tests for it exist and fail in the expected way.
- Don't change the HLD without proposing the change first.

### Navigation

| What you need | Where to look |
|---|---|
| High-level design | `docs/high-level-design.md` |
| Design tree (sub-HLDs, LLDs, their specs) | `docs/intent/` — one folder per node |
| EARS specs | beside each design doc as `{node}-specs.md` in the node's folder under `docs/intent/` |
| Decision docs | `docs/decisions/` (project-level) and `docs/intent/<segment>/decisions/` |

### Terminology

- **HLD**: High-Level Design — single project-level doc at `docs/high-level-design.md`.
- **LLD**: Low-Level Design — detailed component design doc in `docs/intent/`. The design layer is a recursive tree: the root is the HLD, leaf LLDs own EARS, and a component deep enough to outgrow one doc becomes a sub-HLD (HLD-shaped, owns no EARS) with children beneath it. "HLD" and "LLD" are roles by position; depth-2 (one HLD over flat leaf LLDs) is the default.
- **EARS**: Easy Approach to Requirements Syntax — structured one-line requirements beside each design doc as `{node}-specs.md` in the node's folder under `docs/intent/`. IDs are path-concatenated — the root-to-leaf path of the owning segment plus a number — so a prefix grep gathers a subtree. Markers: `[x]` implemented, `[ ]` active gap, `[D]` deferred.
- **Arrow**: the unidirectional chain from vision to code (HLD → LLDs → EARS → Tests → Code). Strictly a DAG of intent.
- **Arrow segment**: the territory owned by one leaf LLD — the LLD itself plus the specs, tests, and code that cite its EARS IDs. The boundary is the leaf prefix. Within-segment cascade is free; across-segment cascade pauses.
- **Cascade**: propagating a change downstream through the arrow so adjacent levels stay coherent.

### Code annotations

Annotate code and tests with `@spec` comments citing EARS IDs:

```python
# @spec SCREEN-MASK-001, SCREEN-MASK-002
```

Place the annotation at the *entry point of the behavior's implementation graph* — the topmost function or module owning the specified behavior, not every helper. When a behavior spans multiple subsystems (UI + API + database, for example), annotate at the entry point in each subsystem. Tests follow the same rule: annotate the test that directly exercises the spec, not every inner assertion.

## Repo conventions

- **Layout:** `src/urbanbio/` (package), `tests/`, `docs/`, `scripts/` (repo tooling),
  `.github/workflows/` (CI). Module layout is defined in the LLD.
- **Naming:** modules and functions `snake_case`, classes `PascalCase`, constants
  `UPPER_SNAKE_CASE`. EARS IDs are uppercase, path-concatenated (see Terminology).
- **Commits:** [Conventional Commits](https://www.conventionalcommits.org/), e.g.
  `feat(ingest): ...`, `fix(screen): ...`, `docs(intent): ...`, `test: ...`, `ci: ...`,
  `chore: ...`. One logical change per commit.
- **Config:** public, committed defaults live in `config/`. Private values (exact coordinates,
  bucket names, account IDs) live in `config/private/` or `*.local.toml`, which are gitignored.
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
