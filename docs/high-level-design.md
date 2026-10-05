# urban-biomonitoring — High-Level Design

**Status:** Approved baseline v1.0 (2026-10-03). Changes go through review.
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

## 2. Architecture overview

```
  Recorder media ──► [1 Ingest] ──► [2 Speech screen] ──► [3 Detect] ──► [4 Validate] ──► [5 Curate] ──► [6 Publish]
                      adapters       mask speech           detector       sample, label    tables,        static site,
                      metadata       archive originals     all scores     calibrate        joins          DwC-A export
                         │                  │                  │               │               │
                         └──────────────────┴────── AWS S3 (system of record) ─┴───────────────┘
```

- **Execution model:** a Python CLI run locally or in a Codespace, stage by stage or end to
  end. No servers.
- **System of record:** AWS S3. Local disk holds only working copies and the quarantine (§5.1).
- **Idempotency:** every stage is keyed by content checksums and run IDs. Re-running a stage
  on the same inputs is a no-op unless parameters or versions change.
- **Provenance:** every stage writes a run record (inputs, outputs, code commit, model and
  parameter versions).

## 3. Components

### 3.1 Ingest

- Reads a recorder's media (e.g., an SD card) into a local quarantine area.
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
- Only masked audio leaves quarantine (§5.2).

### 3.3 Detect

- Runs BirdNET Analyzer in batch on masked audio, using location and date filtering.
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
| AudioFile | One recording: UTC start, duration, sample rate, original and masked checksums, storage location, and tier. |
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
| Quarantine | Local disk only | Unmasked originals as copied from media | Deleted once masking and AWS S3 archival are verified |
| Archive | AWS S3 | Speech-masked originals (lossless FLAC) + verbatim header sidecars | Indefinite; lifecycle-transitioned to a Glacier tier after a configurable age |
| Curated | AWS S3 | Parquet tables, calibrations, labels, run records | Indefinite, Standard tier |
| Clips | AWS S3 | Short masked detection clips for review and dashboard | Indefinite, Standard tier |
| Published | GitHub Pages / AWS S3 public prefix | Dashboard, public Parquet, DwC-A, approved clips | Versioned releases |

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

```
s3://<bucket>/archive/site=<site_id>/deployment=<dep_id>/date=<YYYY-MM-DD>/<file>.flac
s3://<bucket>/archive/.../<file>.header.json
s3://<bucket>/curated/<table>/...parquet
s3://<bucket>/clips/<detection_id>.flac
s3://<bucket>/runs/<run_id>.json
```

### 5.4 Cost controls

Lossless compression, lifecycle transition of archived audio to a Glacier tier, local compute,
a static dashboard, and an AWS Budgets alert. The specific tier and transition age are chosen
in the LLD against current AWS pricing (retrieval latency vs. cost, minimum storage
durations).

## 6. Privacy controls

### 6.1 Speech

- Two-detector screening with padding (§3.2), measured against a hand-labeled sample for
  speech recall.
- Unmasked audio never leaves the local quarantine.
- Published clips get a second speech check.

### 6.2 Location

- Exact site coordinates live in private configuration that is never committed to the public
  repository.
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

- Python with uv, Pydantic data contracts, pytest, GitHub Actions CI, Codespaces dev container,
  `CLAUDE.md` for agent guidance, design docs in `docs/`, MIT license.
- Configuration is declarative (one file per environment); secrets come only from the
  environment.
- **Licensing:** the BirdNET model has its own non-commercial license, so it's installed as a
  dependency and never vendored. Published data license is decided before the first release.

## 9. Technical risks

| Risk | Mitigation |
|------|------------|
| Speech reaches the archive or the public | Two detectors, padding, measured recall, quarantine boundary, clip re-check |
| Detector false positives distort outputs | Per-species calibration; unvalidated taxa flagged |
| Clock drift or time-zone errors | UTC normalization, recorded offsets, drift checks at each retrieval |
| Vendor metadata lost in format conversion | Verbatim header sidecars, checksums of originals |
| Model updates change results | Model version in every detection; runs never overwrite |
| Storage cost creep | Lifecycle tiering, compression, budget alerts |

## 10. Open technical decisions

| ID | Decision | Proposed default |
|----|----------|------------------|
| T1 | Glacier tier and transition age for archived audio | Glacier Instant Retrieval after 60 days |
| T2 | Stored detection confidence floor | 0.1 |
| T3 | Labeling tool | Evaluate an existing open-source tool first |
| T4 | Published coordinate precision | 0.01° |
| T5 | Speech padding and thresholds | ±2 s; tune on labeled sample |
| T6 | Published data license | CC BY 4.0 |
