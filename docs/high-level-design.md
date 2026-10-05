# urban-biomonitoring — High-Level Design

**Status:** Approved baseline v1.6 (2026-10-05). Changes go through review.
**Process:** Linked-Intent Development. This HLD → low-level design → EARS requirements → tests → code.
**Scope of this document:** the software system: architecture, components, data, storage,
privacy controls, and extension points. Field protocols, site details, and project planning
are out of scope.

---

## 1. Purpose

An open-source pipeline that turns passive acoustic recordings from a small network of
field recorders into validated, standards-aligned species occurrence data and a public,
researcher-facing dashboard.

### 1.1 Goals

- **G1 Reproducible processing:** any published number can be traced to its inputs, code
  version, model version, and parameters, and regenerated from the archive.
- **G2 Calibrated detections:** detector output comes with measured, per-species precision.
- **G3 Privacy by construction:** human speech never reaches long-term storage or publication,
  and published locations are generalized.
- **G4 Standards alignment:** outputs use established biodiversity standards (Darwin Core;
  Camtrap DP for the image branch).
- **G5 Sensor-agnostic core:** new recorder types, and later camera traps, plug in through
  adapters without changes to downstream stages.
- **G6 Low running cost:** no always-on infrastructure; storage cost controlled through tiering.

### 1.2 Non-goals

- Real-time or streaming detection.
- Training new species classifiers (the pipeline uses existing models).
- Abundance estimation. Outputs measure **vocal activity**, and the system labels them that way.

### 1.3 Analytical use cases

The data model must support, without restructuring:

- Species presence over time per site (phenology, migration timing).
- Diel and seasonal activity patterns.
- Comparisons between sites.
- Effects of covariates (weather, human noise) on detected activity.
- Comparison with external occurrence data (e.g., eBird).

### 1.4 Tenets

Tie-breakers for decisions no requirement covers. When two conflict, the higher one wins.

1. **Privacy over data.** When it is uncertain whether audio contains speech, mask it. Losing
   some non-speech audio is acceptable; leaking speech is not.
2. **Reproducibility over storage cost.** Keep every run, score, and parameter set rather than
   pruning them to save money.
3. **Stop rather than guess.** When a stage cannot verify something (a checksum mismatch,
   unparseable metadata, an ambiguous timestamp), it stops and flags the affected input instead
   of continuing on a best guess.
4. **A documented manual step beats automation for rare operations.** Weekly media transfer and
   one-time storage setup are runbook steps, not automated infrastructure.
5. **Configuration over detection.** The software acts on its configuration and environment
   variables, never on what it can infer about the host it runs on. When behavior must differ
   between deployments, it differs through a configured value with a documented default.

## 2. Architecture overview

```
  Recorder media ──► [1 Ingest] ──► [2 Speech screen] ──► [3 Detect] ──► [4 Validate] ──► [5 Curate] ──► [6 Publish]
                      adapters       mask speech           detector       sample, label    tables,        static site,
                      metadata       archive masked audio  all scores     calibrate        joins          DwC-A export
                         │                  │                  │               │               │
                         └──────────────────┴──── Store (system of record) ────┴───────────────┘
                                                  AWS S3 by default; pluggable (§5.5)
```

- **Execution model:** a Python CLI, run stage by stage or end to end, on any host that meets
  the prerequisites (§8). No servers. Behavior is selected by a **configuration profile** named
  by the `URBANBIO_PROFILE` environment variable:
  - **`processing`** handles real recorder media. The host running it (the **processing host**)
    holds the quarantine (§5.1) on its local disk and reads recorder media from a local path.
  - **`dev`** runs tests and builds against synthetic sites and synthetic or public-domain
    audio. It refuses production data locations and the commands that read real media or
    write to the archive.

  Nothing in the software depends on which host runs a profile (Tenet 5). An operator's
  concrete hosts, and how private values reach them, are described in runbooks as example
  deployments.
- **System of record:** the configured **store** (§5.5), AWS S3 by default. The processing
  host's local disk holds only working copies and the quarantine (§5.1); losing that host loses
  nothing authoritative. Keeping the store durable is the operator's responsibility.
- **Idempotency:** every stage is keyed by content checksums and run IDs. Re-running a stage
  on the same inputs is a no-op unless parameters or versions change.
- **Provenance:** every stage writes a run record (inputs, outputs, code commit, model and
  parameter versions).

## 3. Components

### 3.1 Ingest

- Reads a recorder's media (e.g., an SD card) into the quarantine (§5.1).
- Computes a SHA-256 for every file before anything else touches it.
- A **recorder adapter** parses device-specific file naming, header metadata, and device logs
  into the common metadata contract (§4). Initial adapter: Wildlife Acoustics Song Meter
  Micro 2. Planned: AudioMoth.
- Normalizes all timestamps to UTC, recording the device's configured time zone and any known
  clock offset.
- Creates a **media retrieval** record within a **deployment**.

### 3.2 Speech screen

- Runs two independent speech detectors on every file and flags a segment if either fires:
  1. the detector model's human-vocalization classes, run in screening mode;
  2. a dedicated voice-activity detector (e.g., Silero VAD).
- Adds configurable padding around flagged segments.
- **Masks** flagged segments by replacing the samples with silence. File length, sample rate,
  and timing stay unchanged, so all offsets remain valid.
- Writes a speech-event log (file, offsets, detector, score) with no audio content.
- Only masked audio leaves quarantine. The archive step encodes masked audio as FLAC, writes the
  header sidecar, writes both to the store, and verifies fidelity in the store before the
  original is purged from the quarantine (§5.2).

### 3.3 Detect

- Runs BirdNET (V2.4 model) through the `birdnet` Python library in batch on masked audio, and
  flags each detection against the species list expected for the site's location and date.
- Stores **every** detection above a low configurable floor, with confidence, model name and
  version, and analysis parameters.
- Keeps non-target classes the model provides (e.g., engine, dog, human) as noise covariates.
- The detector sits behind an interface, so other models (e.g., Perch) or a re-run with a new
  model version produce a new run without overwriting earlier ones.

### 3.4 Validate

- **Sampling:** draws detections for review stratified by species × confidence bin.
- **Labeling:** reviewers see the clip and spectrogram and record a verdict (correct /
  incorrect / unsure, plus the true species if different). The tool is chosen in the LLD:
  either an existing open-source annotation tool or a minimal local UI.
- **Calibration:** per species and model version, fits P(correct) against confidence
  (logistic regression, following Wood & Kahl 2024, *Journal of Ornithology*). Outputs the
  threshold at a target precision and the label count behind it.
- **Inter-recorder agreement:** when recorders are co-located, compares their detections over
  matched time windows to measure between-unit variability.
- Species without a calibration are carried through as *unvalidated*, never silently dropped
  or silently trusted.

### 3.5 Curate

- Builds the analysis tables (§4) as Parquet.
- Joins hourly weather from NWS observations at the nearest station.
- Computes daily and weekly summaries using calibrated thresholds, and records which
  calibration version each summary used.

### 3.6 Publish

- **Dashboard:** a static site (e.g., GitHub Pages) that queries published Parquet directly in
  the browser via DuckDB-WASM. Views: species by site, phenology, diel activity, site
  comparison, covariates, validation and calibration status, methods and caveats, and data
  downloads.
- **Exports:** Parquet tables and a Darwin Core Archive.
- **Clips:** only short detection clips cut from masked audio, each re-checked for speech
  before publication.
- **Locations:** only generalized coordinates (§6.2).

## 4. Data model (conceptual)

Field-level schemas are defined in the LLD as Pydantic models.

| Entity | Description |
|--------|-------------|
| Site | Stable location. Exact coordinates are private; generalized coordinates are published. |
| Deployment | One recorder at one site for a continuous period: device model and serial, firmware, recording configuration, mounting notes. Modeled after Camtrap DP deployments for reuse by the image branch. |
| MediaRetrieval | One media pull within a deployment: time span, file count, checksums, battery status, anomalies. |
| AudioFile | One recording: UTC start, duration, sample rate, original and masked checksums, and store-relative location. On the `s3` backend, its current storage tier is derived from file age and the lifecycle rule. |
| SpeechEvent | One masked segment: file, offsets, detector, score. No audio. |
| Detection | File, offsets, taxon (scientific + common name), confidence, model and version, run ID. |
| Label | A reviewer's verdict on a detection. |
| Calibration | Per taxon × model version: fitted curve, thresholds, label count. |
| WeatherObs | Hourly observations from the nearest NWS station. |
| NoiseEvent | Known or detected human noise (from model classes or manual annotation). |
| DailySummary | Site × date × taxon: calibrated counts, active minutes, first and last detection. |
| Run | Provenance record for one stage execution. |

**Formats:** Parquet for tables, GeoParquet where geometry is needed, DuckDB for analysis.
PostGIS only if a server becomes necessary (not planned).

**Standards:** occurrences map to Darwin Core (`occurrenceID`, `eventDate`, `scientificName`,
`basisOfRecord = MachineObservation`, `identificationVerificationStatus`, generalized
`decimalLatitude`/`decimalLongitude`, `coordinateUncertaintyInMeters`, `dataGeneralizations`),
keeping GBIF publication possible.

## 5. Storage design

### 5.1 Tiers

| Zone | Location | Contents | Retention |
|------|----------|----------|-----------|
| Quarantine | Processing host's local disk only, at the configured `paths.quarantine`, outside the repository working tree | Unmasked originals as copied from media | Deleted once masking and archival in the store are verified |
| Archive | Store | Speech-masked originals (lossless FLAC) + verbatim header sidecars | Indefinite; on the `s3` backend, lifecycle-transitioned to a Glacier tier after a configurable age |
| Curated | Store | Parquet tables, calibrations, labels, run records | Indefinite |
| Clips | Store | Short masked detection clips for review and dashboard | Indefinite; withdrawn when a re-mask covers them |
| Published | GitHub Pages | Dashboard, public Parquet, DwC-A, approved clips | Versioned releases |

The quarantine is on the processing host's local disk, at a configured path outside the
repository working tree so no git operation can stage its contents. The directory has mode `0700`, every intake path is resolved and checked against it,
and originals are purged only after archival is verified. Its capacity is bounded by a
configured disk budget, so ingest processes media in batches that fit (sized in the LLD).

The store is private on every backend: nothing in it is served publicly, and only principals
the operator authorizes can read or write it. Public material is served only from GitHub Pages.

### 5.2 Archive fidelity

The archive is the pipeline's **reprocessing source**. Any stage after speech screening can be
re-run from it.

- Every sample outside masked speech intervals is bit-identical to the original. FLAC is
  lossless, and masking only zeroes flagged intervals.
- Device header metadata that doesn't carry into FLAC (e.g., vendor-specific WAV chunks) is
  preserved verbatim in a sidecar file.
- Each archived file records the SHA-256 of the original, the SHA-256 of the masked version,
  and the speech-event IDs applied to it.
- **Accepted trade-off:** speech false positives permanently remove a small amount of
  non-speech audio. Detector padding and thresholds are tuned with that cost in mind, and the
  speech-event log measures how much audio was masked.
- If speech detection improves later, archived audio can be re-screened and re-masked in
  place, with versioned run records.

### 5.3 Layout (indicative)

Keys are relative to the store root (`s3://<bucket>/` on the `s3` backend, the configured root
directory on the `filesystem` backend) and identical on every backend:

```
archive/site=<site_id>/deployment=<dep_id>/date=<YYYY-MM-DD>/<file>.flac
archive/.../<file>.header.json
curated/<table>/...parquet
retrievals/site=<site_id>/deployment=<dep_id>/<retrieval_id>/...   (device logs, manifest)
clips/<detection_id>.flac
runs/<run_id>.json
runs/<run_id>.log
.urbanbio-store.json                                               (store marker, §5.5)
```

### 5.4 Cost controls

Lossless compression, compute on the operator's own host, and a static dashboard on every
backend. On the `s3` backend, also lifecycle transition of archived audio to a Glacier tier and
an AWS Budgets alert; the specific tier and transition age are chosen in the LLD against
current AWS pricing (retrieval latency vs. cost, minimum storage durations).

### 5.5 Store backends

Stages never address storage directly. They read and write through a **store interface**
(put with integrity verification, get, head for an object's metadata, list, and delete, which
is permitted only under `clips/`), and the backend is chosen by the `[store]` table of the
profile's configuration (§8). A new backend implements the interface and changes no stage. One contract test suite runs
against every backend.

| Backend | Store root | Integrity and protection | Notes |
|---|---|---|---|
| `s3` (default) | A private AWS S3 bucket, named by `URBANBIO_BUCKET` | Checksum-verified writes confirmed by a metadata read; an IAM policy that denies the pipeline deletes outside `clips/` | Glacier lifecycle tiering, budget alerts. Accessed through the AWS API, not a filesystem mount. |
| `filesystem` | A configured directory on any mounted filesystem (a lab NFS or SMB share, an external disk, or a FUSE mount of object storage) | Write to a temporary name, fsync, atomic rename, read-back verification; archive files written read-only | No tiering; durability and access control come from the filesystem the operator provides. |

**Store marker.** `urbanbio store init` writes `.urbanbio-store.json` at the store root,
recording the profile the store belongs to. Every command that touches the store first reads
the marker and refuses to run when it is missing (for example, an unmounted share leaves an
empty directory) or names a different profile. This is how the `dev` profile refuses
production data on any backend.

Volume, for sizing a store: about 7 GB of masked FLAC per recorder-week (an estimate until the
first real card is measured), so about 730 GB per year for two recorders. Detailed sizing and
cost are in the archive LLD.

## 6. Privacy controls

### 6.1 Speech

- Two-detector screening with padding (§3.2), measured against a hand-labeled sample for
  speech recall.
- Unmasked audio never leaves the quarantine. Because the quarantine is on the processing
  host's local disk, unmasked audio never leaves that host: it is never committed, never written
  to the store, and never copied to another host or to a folder that the host syncs or backs up.
  Listening to held originals for speech labeling happens only on the processing host.
- The recorder's media is wiped and reused only after archival is verified in the store. On the
  `filesystem` backend, the store must also be on a different filesystem from the quarantine,
  so wiping the card never leaves the archive on the same disk as the working copies.
- Published clips get a second speech check.

### 6.2 Location

- Exact site coordinates live in private configuration read from an environment variable
  (§8), never from a file in the repository. They never appear in tables, run records, or logs.
  Positions that recorders write into their own file headers and logs survive only in the
  private store (header sidecars and device logs), which is never published.
- Published coordinates are generalized (e.g., rounded to 0.01°, ~1 km), with the
  generalization declared in Darwin Core fields.
- Public site identifiers are opaque (`site1`, `site2`, …).

### 6.3 Repository hygiene

- Secrets and private configuration are excluded through `.gitignore` and a CI check.
- Test fixtures use synthetic or public-domain audio only.

## 7. Extensibility

- **Recorder adapters:** one adapter per device family behind a common interface (AudioMoth
  next).
- **Detector interface:** BirdNET first; other acoustic models, or ultrasonic bat classifiers,
  later.
- **Image branch:** camera traps reuse Site / Deployment / MediaRetrieval / Run, the privacy
  pattern (person-class filtering via MegaDetector or SpeciesNet), and the validation and
  calibration framework, with output in Camtrap DP.
- **Future analyses:** soundscape indices, external occurrence comparison, and additional
  covariates (e.g., land cover) are added as curate-stage tables, not core changes.

## 8. Engineering standards

- Python with uv, Pydantic data contracts, pytest, GitHub Actions CI, an optional dev container
  definition, `CLAUDE.md` for agent guidance, design docs in `docs/`, MIT license.
- **Prerequisites:** Linux on x86_64 (tested on Debian 13 and Ubuntu 24.04 LTS; other
  distributions best-effort); the Python and uv versions pinned in the repository; the system
  packages listed in `scripts/system-packages.txt`; and a store, which by default means an AWS
  account with S3 access. As guidance, a processing host needs at least 8 GB of RAM for the
  default worker count and, per recorder-week processed at once, about 12 GB of working disk
  beyond about 10 GB for dependencies, models, and free-space margin. The README and the install
  LLD carry the full list.
- **Configuration** is declarative, in three layers:
  1. committed stage parameters (`config/pipeline.toml`), identical in every profile so a result
     never depends on where it was computed (G1);
  2. one committed file per profile (`config/<profile>.toml`) with documented defaults for
     paths, worker count, disk budget, and the store backend, sized for the minimum host above;
  3. an optional operator file outside the repository working tree, named by `URBANBIO_CONFIG`,
     which may override only those deployment tables.

  Private values (`URBANBIO_SITES`; `URBANBIO_BUCKET` and the AWS credentials on the `s3`
  backend) are read only from environment variables. How they reach the environment (an env
  file, a secrets manager, a hosted-development secret store) is an operator concern documented
  in runbooks. The `dev` profile uses committed synthetic sites and refuses a set
  `URBANBIO_SITES`. `urbanbio config check` enforces the host-independent safety rules:
  configured paths lie outside the repository working tree, the quarantine, work, and state
  directories have mode `0700`, the store marker matches the profile, and private values are
  never printed.
- **Reproducible install:** a host can be set up from the repository alone. A committed,
  idempotent installer for Debian and Ubuntu hosts installs the listed system packages, the
  pinned uv, and locked dependencies, and creates the configured data directories. It does not
  create secret stores or edit shell startup files. CI runs it on standard Debian and Ubuntu
  images, so it can't break unnoticed. The dev container reads the same package list and pins.
  Each processing run starts from the code and lockfile CI tested.
- **Licensing:** BirdNET models carry their own non-commercial license (CC BY-NC-SA 4.0), so
  they are downloaded by the `birdnet` library at runtime and never vendored. Published data
  license is decided before the first release.

## 9. Technical risks

| Risk | Mitigation |
|------|------------|
| Speech reaches the archive or the public | Two detectors, padding, measured recall, quarantine boundary, clip re-check |
| Detector false positives distort outputs | Per-species calibration; unvalidated taxa flagged |
| Clock drift or time-zone errors | UTC normalization, recorded offsets, drift checks at each retrieval |
| Vendor metadata lost in format conversion | Verbatim header sidecars, checksums of originals |
| Model updates change results | Model version in every detection; runs never overwrite |
| Storage cost creep | Lifecycle tiering, compression, budget alerts |
| Processing host sleeps or loses power mid-run | Idempotent, resumable stages (ledger in the store); the card is the backup until safe to wipe |
| Memory exhaustion on a small processing host | Configurable worker count with a documented, conservative default and RAM guidance; audio stages stream one file per worker; peak memory recorded in every run |
| Processing host can't be rebuilt | Installer in the repo, tested in CI on standard images; nothing authoritative on the host |
| Store unavailable or unmounted | Store marker checked before any store access; commands refuse to run rather than write to an empty mount point |
| Archive lost on a `filesystem` store | Operator-provided durability; card wipe requires the store on a different filesystem from the quarantine |

## 10. Technical decisions

| ID | Decision | Resolution | Details |
|----|----------|------------|---------|
| T1 | Glacier tier and transition age for archived audio (`s3` backend) | S3 Glacier Instant Retrieval after 30 days | `docs/intent/archive/` § Cost |
| T2 | Stored detection confidence floor | 0.10 | `docs/intent/detect/` |
| T3 | Labeling tool | Evaluate Whombat first, then Label Studio; a minimal local UI only if both fail | `docs/intent/validate/` |
| T4 | Published coordinate precision | 0.01°, declared as `coordinateUncertaintyInMeters` = 1000; per-site overrides may only be coarser | `docs/intent/publish/` |
| T5 | Speech padding and thresholds | ±2 s padding; BirdNET `Human vocal` ≥ 0.10; Silero VAD ≥ 0.30; retuned against measured speech recall | `docs/intent/speech-screen/` |
| T6 | Published data license | CC BY 4.0, after confirming the BirdNET model license (CC BY-NC-SA 4.0) places no conditions on detection data; CC BY-NC 4.0 otherwise | `docs/intent/publish/` |
