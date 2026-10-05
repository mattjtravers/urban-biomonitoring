---
parent: ingest
prefix: INGEST-INTAKE
---

# Intake and Quarantine

## Context and Design Philosophy

Intake is the only part of the pipeline that handles a card's raw contents. It owns the
quarantine (HLD §5.1): a directory on the processing host's local disk at `paths.quarantine`
([config-cli](../../config-cli/config-cli-design.md) § Configuration), outside the repository
working tree. Unmasked audio enters the quarantine and is deleted there; it never leaves the
quarantine, and so never leaves the processing host.

Intake does five things, in order: fix each file's identity with SHA-256 before anything
parses it; register the retrieval; normalize every timestamp to UTC; record what the card says
about the deployment's health; and, at the end of the pipeline, purge originals that the archive
has verified.

## Transfer into the Quarantine

The recorder's microSD card is read by the processing host and copied straight from the
card's mount point into the quarantine. This is a manual runbook step (Tenet 4). Where the card
is mounted depends on the host; the deployment runbooks give the path for each example host.

```bash
mkdir -p <paths.quarantine>/incoming/<label>
cp -r --preserve=timestamps <card mount>/. <paths.quarantine>/incoming/<label>/
```

Before ingest, the copy is compared with the card byte for byte:

```bash
diff -rq <card mount>/ <paths.quarantine>/incoming/<label>/
```

Any output means the copy is incomplete or corrupt. The copy is deleted and repeated. Ingest's
hashes are computed from the copy, so this comparison is the only check that the copy matches
the card.

The copy goes from the card's mount directly into `incoming/`. It is never staged anywhere
else on the host, in particular not in any folder the host syncs to a cloud service or backs
up. No other copy is made. A host-level backup or snapshot that would include the quarantine is
taken only when the quarantine holds no audio. The deployment runbooks name each example host's
synced folders and backup features. The card isn't wiped until `urbanbio retrieval status`
reports it safe (below).

## Quarantine Layout

```
<paths.quarantine>/
  incoming/<label>/               # As copied; <label> is any name the operator chooses
  <retrieval_id>/
    card/                         # The card contents, moved here from incoming/<label>/
    manifest.json                 # Paths, sizes, SHA-256 of every file (no audio content)
  speech-labeling/<audio_file_id>.wav   # Hard links to originals held for speech labeling
```

The quarantine directory is created with mode `0700`. Every intake path argument is resolved
(symlinks followed) and must lie under the resolved `paths.quarantine`. Anything else is
refused.

## Ingest Flow

`urbanbio ingest PATH --deployment D --retrieved T [--clock-offset-s X] [--retrieval R]`

1. **Hash first.** Walk `PATH`, compute SHA-256 of every file (streamed in 1 MiB blocks), and
   write `manifest.json`. No parser opens a file before its hash is recorded.
2. **Register the retrieval.** Allocate the next `retrieval_id` for the deployment, or reuse
   `--retrieval R` to add files from the same card copied in parts. Move `PATH` to
   `<retrieval_id>/card/` (a rename on the same filesystem).
3. **Classify** the card with the deployment's adapter (`list_media`). Unknown files are
   recorded as an `unknown_file` anomaly and left in place.
4. **Per audio file** (one unit each):
   - Compute `audio_file_id` from the SHA-256. If the file-status lookup
     ([runs](../../runs/runs-design.md) § Lookups) shows that ID already archived, skip it as a
     duplicate (a card ingested twice).
   - Read metadata through the adapter, and compute `original_pcm_sha256` over the decoded
     samples.
   - Check the container: the RIFF and `data` chunk sizes declared in the header must match
     the bytes present. A shortfall flags the file `truncated` (typically a copy interrupted
     mid-file, or a recording cut off by battery failure). When the card itself holds the
     truncated file (power loss), the operator waives the check with
     `urbanbio runs unflag ingest UNIT --reason truncated --note TEXT`
     ([runs](../../runs/runs-design.md) § Waivers), and the frames present are screened and
     archived like any other file.
   - Check the file against the deployment: sample rate, serial, and recorder name match;
     bit depth is 16 and channels is 1 (otherwise flag `unexpected_format`). If the header has
     a device position, compare it to the site's private coordinates (below).
   - Normalize time (below) and build the AudioFile record.
   - Check the file lies within the deployment: `start_utc` no earlier than the deployment's
     `start_utc` minus 10 minutes, and the file end no later than its `end_utc` (when set) or
     `--retrieved` plus 10 minutes. Otherwise flag `outside_deployment`. Recordings made
     before the recorder was placed (for example, testing at home) are the main case, and they
     may contain speech, so they are never archived automatically.
   - Resolve same-name conflicts (below).
5. **Device log.** Parse it through the adapter and derive battery start/end/minimum, reboot
   count, expected file count, and anomalies (below). Device-log times are device-local with no
   recorded offset. Each row is converted with the offset of the audio files from the same
   power-on session (the files whose start times fall within the session), falling back to the
   deployment's `schedule.utc_offset_minutes`.
6. **Write** `media_retrievals` and `audio_files` rows and the ingest ledger.

## Time Normalization

All times become UTC at ingest (HLD §3.1). Adapters return device-local times and, when the
device records one, the UTC offset.

- **Offset source.** The per-file offset reported by the adapter is authoritative. If the
  adapter reports none, the deployment's `schedule.utc_offset_minutes` is used. If neither
  exists, the file is flagged `timezone_unknown` (Tenet 3).
- **Fixed offsets.** Recorders that hold one UTC offset for a whole deployment (the Song Meter
  Micro 2 does; see its LLD) produce continuous UTC times across a daylight-saving change with
  no special handling. Intake never applies a named time zone's DST rules to device time.
- **Offset changes.** Files within one retrieval that report different offsets are kept, and
  each is normalized with its own offset. An `offset_change` anomaly records where the change
  occurred.
- **Consistency.** When the filename time and the header time disagree by more than 1 second,
  the file is flagged `timestamp_mismatch`.
- **Clock drift.** `--clock-offset-s X` records the device clock minus a reference clock,
  measured at retrieval (positive means fast). The device clock is assumed correct when last
  synchronized at the previous retrieval or deployment start (`t0`) and off by `X` at this
  retrieval (`t1`). Each file gets a linear correction
  `clock_correction_s = -X × (t − t0) / (t1 − t0)`, applied when `|X| ≥ 0.5 s`. When
  `|X| > 60 s` the retrieval gets a `clock_drift` anomaly and the deployment's
  `timestamp_issues` is set. Without `--clock-offset-s`, no correction is applied and
  `clock_offset_s` is null.

## Same-Name Conflicts

Within one retrieval, two audio files with the same `original_filename` but different SHA-256
are two copies of one recording, typically a truncated first copy and a complete re-copy
ingested with `--retrieval R`:

- If exactly one copy passes all checks, it is kept. The other is flagged `superseded`, and
  its ledger row records the `audio_file_id` that supersedes it.
- If both pass, or both fail, both are flagged `filename_conflict` for the operator to
  resolve.

## Retrieval Health

From the device log and the file list:

- **Gaps:** consecutive file starts further apart than `duty_on_s + duty_off_s + 5 s` (or
  `max_file_length_s + 5 s` for continuous schedules), outside configured daily windows.
- **Reboots:** device-log sessions after the first.
- **File count:** the sum of files the log says were completed, compared with audio files
  present. Any difference gives a `file_count_mismatch` anomaly.
- **Low battery:** any logged voltage under the adapter's low-voltage threshold.

Anomalies are records, not failures. Only per-file problems flag units.

## Site Position Check

The comparison runs inside the configuration layer: `config.site_offset_m(site_id, lat, lon)`
returns a distance in meters. Neither the site's exact coordinates nor the device position
cross into stage code or persisted records. Distances over 500 m give a `position_mismatch`
anomaly. A missing or zero device position skips the check.

## Disk Budget

Sizing at 48 kHz, 16-bit, mono, 1 minute on / 4 minutes off around the clock:

| Quantity | Value |
|---|---|
| Bytes per recorded minute (WAV) | 5.76 MB |
| Recorded minutes per day per recorder | 288 |
| WAV per recorder-day | 1.66 GB |
| WAV per recorder-week (one weekly card) | 11.6 GB |

The processing host's disk is shared with the OS, Python dependencies, the uv cache, and
model caches. The quarantine and work directories together may use at most
`quarantine.budget_gb`, and the filesystem must keep `quarantine.min_free_gb` free (both in
the profile's configuration, overridable in the operator config file). The defaults are sized
for a host with about 50 GB free before the pipeline's dependencies are installed
([install](../../install/install-design.md) § Sizing guidance):

| Use | GB |
|---|---|
| Python dependencies (CPU `torch`, LiteRT, `scipy`, `pyarrow`, `duckdb`) and the uv cache | ~6 (estimate) |
| BirdNET and Silero models, test-audio cache | < 1 |
| `quarantine.min_free_gb` | 3 |
| Margin | ~4 |
| `quarantine.budget_gb` | 35 |

A budget of 35 GB holds two weekly cards (23.2 GB) at once but not three, so the weekly
procedure can copy both recorders' cards before processing. They are still **processed one at a
time**: only one pipeline command runs at a time
([runs](../../runs/runs-design.md) § Concurrency), so the second card's `urbanbio process` is
started after the first finishes. **TODO: measure on the first real card**: the installed
dependency footprint and uv cache size, which settle the budget.

Peak usage is the card's originals. The archive stage purges each original as soon as its
masked FLAC is verified ([archive](../../archive/archive-design.md) § Archive Flow), so the
work directory's FLACs (about 60% of WAV size) grow while the originals shrink, and the total
never exceeds the card's WAV size. Detect then deletes each FLAC after analyzing it. When a card holds more than
fits (for example, a missed weekly retrieval), copy `Data/` in date ranges (the filenames start
with the date) and ingest each part with `--retrieval R`.

`ingest` refuses to start when the quarantine and work directories together already exceed
`quarantine.budget_gb` (the card being ingested is already in `incoming/`, so it counts), or
when free space on the quarantine filesystem is below `quarantine.min_free_gb`. `archive`
refuses to start when free space is below `quarantine.min_free_gb`. Both errors state the
current usage, the budget, and the free space.

## Purge

Purge deletes an original from the quarantine. The archive stage calls it for each file right
after verification. `urbanbio purge --retrieval R` sweeps up anything left (files archived with
`--no-purge`, superseded files, device logs, released speech-labeling holds). An original is
deleted when all of these hold:

1. The archive ledger shows the unit (`audio_file_id`, mask version 1) `succeeded`, which
   includes the bit-identity and store-write checks
   ([archive](../../archive/archive-design.md) § Archive Flow).
2. The file isn't held for speech labeling.
3. The file isn't flagged at any stage.

Two exceptions remove flagged originals:

- A file flagged `superseded` is deleted once the file that supersedes it has been archived.
- `urbanbio purge --retrieval R --discard UNIT [--discard UNIT …]` deletes named flagged files
  without archiving them (for example, `outside_deployment` test recordings). The discard and
  the operator's stated `--reason` are recorded in the purge run. Discarded files are never
  written to the store or anywhere else.

Device logs and diagnostics are purged once archived. When only `manifest.json` remains, it is
written to the retrieval's prefix in the store and the retrieval directory is deleted.

## Card Wipe Readiness

`urbanbio retrieval status R` reports per-file state and prints **safe to wipe** only when
both hold:

1. every manifest audio file is archived and verified in the store, superseded by an archived
   copy, or discarded by the operator;
2. the store is not on the same filesystem as `paths.quarantine`
   ([store](../../store/store-design.md) § Same-Filesystem Rule). This always holds on the `s3`
   backend. On the `filesystem` backend, a store on the quarantine's filesystem means wiping the
   card would leave the archive on the same disk as the working copies.

When either fails, it prints the reason instead. Until then, the card is the backup.

## Speech-Labeling Hold

`urbanbio speech sample` ([speech-screen](../../speech-screen/speech-screen-design.md))
hard-links chosen originals into `quarantine/speech-labeling/` after screening and before
archive, so the sample is taken before per-file purge. `urbanbio process --speech-sample N` does
this as part of the pipeline. Purge skips held files. `speech label --import` releases them, and
the next purge deletes them. A hold older than 30 days is reported by `retrieval status`.

Unknown files on the card (neither audio nor device logs) are archived with the device logs and
purged with them.

## Decisions & Alternatives

| Decision | Chosen | Alternatives Considered | Rationale |
|---|---|---|---|
| Transfer method | Manual `cp` from the card's mount straight into `incoming/` | Dragging in the host's file manager; staging in the host's own folders first; a sync agent; copying the card to the store first | Weekly and manual (Tenet 4). One command that never lands the audio outside the quarantine. Host folders may be synced or backed up, and anything via the store would put unmasked audio outside the quarantine (G3). |
| Hash timing | Before any parsing | Hash while parsing | HLD §3.1: the checksum fixes identity before anything touches the file. |
| DST handling | Use the device's recorded offset; never apply zone rules | Convert filename times with `America/New_York` | Devices that keep a fixed offset would be shifted by an hour across the change if zone rules were applied. |
| Drift model | Linear between synchronizations, from an operator measurement | Ignore drift; acoustic reference events | Linear is simple and adequate for a crystal clock over a week. Acoustic references are a later refinement. |
| Disk pressure | A configured budget (default 35 GB) checked at ingest; cards processed one at a time; date-range parts when a card doesn't fit | A fixed limit of one card on disk; a larger disk | A budget adapts to the host and lets both weekly cards be copied in one sitting. Processing one at a time keeps peak memory and disk use to one card's worth. |
| Purge gate | Archive ledger plus bit-identity check | Store object exists | Existence doesn't prove the archived samples equal the masked original. |
| Files outside the deployment window | Flag; archive or discard only by operator decision | Archive everything on the card | Pre-deployment test recordings are often made indoors near people (Tenet 1). |
| Re-copied files | The copy that passes all checks supersedes the other | Keep both; keep the newest | Only one copy of a recording should reach the archive, and "passes the checks" is verifiable where "newest" is not. |

## Open Questions & Future Decisions

### Deferred
1. **TODO: confirm from first real card:** whether pairing with the Song Meter Configurator
   shows the recorder's clock before resynchronizing it. This decides how `--clock-offset-s` is
   measured at retrieval.
2. Drift estimation from a recurring noise event at a known time of day, as a cross-check on the
   operator measurement.
3. A separate parameter set for a future ultrasonic recorder (250–384 kHz) would change the disk
   budget by more than 5×. Size it when one is added.

## References

- HLD §3.1, §5.1, §6.1, Tenets 3–4
- Losing the processing host and why the card is the backup:
  [config-cli](../../config-cli/config-cli-design.md) § Losing a host
- Weekly card procedure: `docs/runbooks/weekly-card.md`
