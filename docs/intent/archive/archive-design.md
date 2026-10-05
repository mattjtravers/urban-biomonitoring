---
parent: high-level-design
prefix: ARCH
---

# Archive

## Context and Design Philosophy

The archive is the pipeline's reprocessing source (HLD §5.2): every stage after the speech
screen can be re-run from it. It holds speech-masked audio as lossless FLAC, the verbatim
device header of every file, and the device logs, in the store, the system of record
(HLD §2, §5.1, §5.5; [store](../store/store-design.md)).

Three properties are non-negotiable:

1. **Fidelity.** Every sample outside the mask is bit-identical to the original, and that is
   checked before the original is deleted, not assumed.
2. **Only masked audio leaves the quarantine.** The archive write API accepts only `MaskedAudio`
   ([speech-screen](../speech-screen/speech-screen-design.md) § Padding, Merging, and the
   Mask).
3. **Private storage.** Nothing in the store is ever made public. Published material is served
   from GitHub Pages ([publish](../publish/publish-design.md)).

Package: `urbanbio.storage` (`archive.py`, `flac.py`, `sidecar.py`). The archive reads and
writes only through the store interface and builds keys only with `urbanbio.storage.paths`
([store](../store/store-design.md)).

## Archive Flow (per audio file)

`urbanbio archive --retrieval R` processes each screened file:

1. **Re-verify the original.** Read the original from the quarantine and confirm its SHA-256
   still equals `original_sha256`.
2. **Mask.** `apply_mask(original, events)` produces `MaskedAudio` (mask version 1 at first
   archive).
3. **Encode** the masked samples to FLAC in the work directory: `soundfile.write(...,
   format="FLAC", subtype="PCM_16", compression_level=archive.flac_compression_level)`. FLAC
   is lossless at every compression level; the level trades CPU for size only. The FLAC holds no
   descriptive tags, so it carries no device position.
4. **Prove fidelity.** Decode the FLAC and require exact array equality with the masked
   samples. Require that the decoded samples equal the original wherever the mask is false, and
   are zero wherever it is true. Compute `masked_sha256` (FLAC bytes) and `masked_pcm_sha256`
   (decoded samples).
5. **Build the sidecar** (below) from the adapter's `header_chunks`.
6. **Check for an earlier write.** `store.head` the target key. If an object exists with the
   same `mask-version` metadata:
   - same `masked-pcm-sha256`: the write already happened (for example, the run crashed
     before its ledger flush). Skip the write and continue at step 8 with the existing object's
     checksum. Identical samples count as identical even when FLAC bytes differ (for example,
     after a libsndfile upgrade).
   - different `masked-pcm-sha256`: raise `IntegrityError` (`archive_conflict`) and flag. An
     archived version is never overwritten except by a higher `mask_version`.
7. **Write** the FLAC and then the sidecar with `store.put`, passing the precomputed SHA-256,
   `overwrite=False`, and the metadata `audio-file-id`, `mask-version`, `masked-sha256`,
   `masked-pcm-sha256`, `original-sha256`. `put` returns only after the store has confirmed the
   object's length and full SHA-256 ([store](../store/store-design.md) § Store Interface), so a
   returned `put` is the proof that the archived bytes are the verified FLAC.
8. **Record** an AudioMaskVersion row and a `succeeded` ledger unit (unit ID
   `audio_file_id` + mask version). Only this row allows purge
   ([intake](../ingest/intake/intake-design.md) § Purge).
9. **Purge** the original from the quarantine through intake's purge, unless the file is held
   for speech labeling or the run has `--no-purge`. Purging per file keeps the work directory's
   FLACs and the remaining originals within one card's size on disk.

Any failed check raises `IntegrityError`. The unit is flagged, nothing is purged, and the
original stays in the quarantine.

A worker ([runs](../runs/runs-design.md) § Resource Limits) is one process that takes one file
through steps 1–7 at a time. `resources.workers` workers run in parallel. The main process
records each result (step 8) and purges (step 9), so ledger writes and purges happen in one
place.

The work-directory FLAC is kept until the detect stage has processed that file, so detect
needn't download it. Detect then deletes it.

**Device files.** The summary log, diagnostics, and unknown files from the card are written
verbatim to the store under the retrieval prefix ([store](../store/store-design.md) § Key
Layout), after a content check: any file whose header
identifies it as audio (RIFF/WAVE, FLAC, Ogg, MP3) and wasn't processed as an audio file is
flagged `unexpected_audio` and **not written to the store**.

## Header Sidecar

`<audio_file_id>.header.json`, UTF-8 JSON:

```json
{
  "schema": "urbanbio.header-sidecar/1",
  "audio_file_id": "af-3f9a0c1d2e4b5a6c",
  "original_filename": "site1A_20261012_140500.wav",
  "original_sha256": "…",
  "original_bytes": 5760512,
  "container": "RIFF/WAVE",
  "chunks": [
    {"id": "fmt ", "offset": 12, "size": 16, "data_b64": "…"},
    {"id": "guan", "offset": 36, "size": 412, "data_b64": "…"},
    {"id": "data", "offset": 456, "size": 5760000, "data_b64": null}
  ],
  "guano": {"Firmware Version": "…", "Timestamp": "…", "…": "…"},
  "adapter": "songmeter-micro2",
  "adapter_version": 1
}
```

Every non-`data` chunk is stored byte-for-byte (base64). The `data` chunk is listed by offset
and size only. From the sidecar plus the archived FLAC, the original WAV container can be
rebuilt exactly, except that the masked samples are zero. The parsed `guano` object is a
convenience copy. The sidecar includes the device's exact position, so sidecars are private
and never published.

## Re-masking

When [speech-screen](../speech-screen/speech-screen-design.md) re-screening produces a new
mask version:

- The new FLAC (built by `remask`, see
  [speech-screen](../speech-screen/speech-screen-design.md) § Re-screening and Re-masking) goes
  through steps 3–8 with `mask_version + 1` and is written with `overwrite=True` to the same
  key. The sidecar is unchanged, and there's no original to purge.
- The previous masked version contains speech the new mask removes, so the store keeps no copy
  of it (Tenet 1). On `s3`, bucket versioning is disabled; on `filesystem`, the new file
  replaces the old one by rename ([store](../store/store-design.md)). Copies outside the store's
  control, such as snapshots or backups an operator takes of a `filesystem` store, can retain the
  previous version; the filesystem-backend runbook tells operators to expire them after a
  re-mask.
- A new AudioMaskVersion row records the version, checksums, events, and run.

## Keys

Archive objects live under `archive/` and device files under `retrievals/`, in the layout
defined by [store](../store/store-design.md) § Key Layout. On the `s3` backend, bucket
configuration, the lifecycle rule that applies the T1 tier, and the pipeline's IAM policy are in
[store](../store/store-design.md) § `s3` Backend.

## Cost (`s3` backend)

The `filesystem` backend's cost is the operator's own storage. The volume model below sizes
either backend.

Prices: AWS Price List API, `us-east-1`, publication 2026-09-28 (AmazonS3), 2026-09-11
(AmazonS3GlacierDeepArchive), 2026-09-16 (AWSDataTransfer).

| Storage class | USD / GB-month | Minimum storage duration | Retrieval |
|---|---|---|---|
| S3 Standard | 0.023 | — | — |
| S3 Standard-IA | 0.0125 | 30 days | USD 0.01/GB |
| S3 Glacier Instant Retrieval | 0.004 | 90 days | USD 0.03/GB, milliseconds |
| S3 Glacier Flexible Retrieval | 0.0036 | 90 days | Restore first (minutes to hours) |
| S3 Glacier Deep Archive | 0.00099 | 180 days | Restore first: bulk USD 0.0025/GB (within 48 h), standard USD 0.02/GB (within 12 h) |

Requests and transfer: PUT USD 0.005 per 1,000 (Standard); lifecycle transition to Glacier IR
USD 0.02 per 1,000 objects; data transfer out to the internet USD 0.09/GB after the first
100 GB/month (AWS free tier). A processing host outside AWS downloads from the archive as
internet transfer out. Uploads into AWS S3 are free.

**Volume model** (sized for 3 recorders; parameters in config): 48 kHz, 16-bit mono WAV at
288 recorded minutes per day is 1.66 GB per recorder-day. Assuming FLAC reaches 60% of WAV
size on outdoor ambient audio (**TODO: measure on the first real card**), that's about
1.0 GB per recorder-day: 3 GB/day, 90 GB/month, about 1.1 TB/year for 3 recorders, in about
315,000 audio objects a year.

| Monthly storage cost at 1.1 TB (end of year 1) | USD |
|---|---|
| All S3 Standard | 25.0 |
| 30 days in Standard, then Glacier IR | 2.1 + 4.0 = 6.1 |
| 30 days in Standard, then Deep Archive | 2.1 + 1.0 = 3.1 |

Year-1 total for the chosen policy is about USD 60 (storage about 49, PUTs and transitions
about 10). Year 2 is about USD 110.

**Reprocessing a year of audio** (1.1 TB): from Glacier IR, about USD 33 retrieval + USD 89
transfer out. From Deep Archive, about USD 11 restore + USD 89 transfer out, plus restore
orchestration and up to 48 h of waiting. Transfer out dominates either way. Spreading a
non-urgent reprocessing over months keeps each month within the 100 GB free allowance.

### Compute and upload

Processing runs on the operator's own host, so it adds no compute charge, and the cost model
has no compute line.

A weekly card pair is about 14 GB of FLAC (two recorders × 7 days × about 1.0 GB). Upload time
is that volume over the host's upstream bandwidth: about 1.5 hours at 20 Mbit/s, overlapping the
screen and detect compute. **TODO:
measure on the first real card**: upload throughput and its share of wall-clock time
([runs](../runs/runs-design.md) § Resource Limits).

### T1 resolution (HLD §10)

**S3 Glacier Instant Retrieval after 30 days.** Glacier IR is a sixth of Standard's price and
keeps millisecond access, so reprocessing (G1) is a plain read with no restore step to build or
wait for. Deep Archive would save about USD 3/TB-month more, but adds restore orchestration
and 180-day minimums for a few dollars a month at this scale (Tenet 4). Thirty days in Standard
covers the period when re-screening and detector reruns of fresh data are likely, and
overwriting a re-masked file in Glacier IR before 90 days costs only the prorated remainder
(USD 0.004/GB-month). Revisit Deep Archive for data older than two years once the archive
passes about 5 TB.

## Decisions & Alternatives

| Decision | Chosen | Alternatives Considered | Rationale |
|---|---|---|---|
| Audio format | FLAC 16-bit via libsndfile | WAV; W4V (Wildlife Acoustics' compressed format) | FLAC is lossless and open. W4V raises the noise floor [Song Meter Micro 2 UG p.94], which breaks HLD §5.2 fidelity. |
| Fidelity proof | Decode-and-compare before the write; a store write confirmed by full-object SHA-256 | Trust the encoder; compare object size | Purge deletes the only unmasked copy, so fidelity must be proven, not assumed. |
| Previous masked versions | Not kept in the store (`s3` versioning disabled; `filesystem` replace by rename) | Keep versions with expiry | A previous version of a re-masked file still contains the speech the new mask removes (Tenet 1). Deletion protection comes from the store's delete restriction, not from versions. |
| Zones | One store, prefixes per zone | Separate stores or buckets per zone | One marker, one policy, one lifecycle, one budget. The IAM policy scopes by prefix anyway. |
| Public material | GitHub Pages only | A public store prefix | Keeps "nothing in the store is public" absolute and avoids public egress charges. |
| Archive tier (T1) | Glacier IR after 30 days | Standard-IA; Glacier Flexible Retrieval; Deep Archive | See T1 resolution. |

## Open Questions & Future Decisions

### Deferred
1. FLAC compression ratio on real recordings (affects the cost model only).
2. Moving data older than two years to Deep Archive once the archive passes about 5 TB.
3. Cross-region replication or a second-account backup. Not planned: AWS S3 stores data
   redundantly across Availability Zones, and the cost would double.

## References

- HLD §2, §5, §6.1, T1, Tenets 1 and 4
- [store](../store/store-design.md): store interface, key layout, `s3` bucket and IAM
- AWS Price List API (bulk offer files):
  https://pricing.us-east-1.amazonaws.com/offers/v1.0/aws/AmazonS3/current/us-east-1/index.json,
  `…/AmazonS3GlacierDeepArchive/…`, `…/AWSDataTransfer/…`
- Amazon S3 pricing: https://aws.amazon.com/s3/pricing/
- Lifecycle transition constraints (128 KB default, minimum durations, 40 KB archive overhead):
  https://docs.aws.amazon.com/AmazonS3/latest/userguide/lifecycle-transition-general-considerations.html
- Checking object integrity (additional checksums):
  https://docs.aws.amazon.com/AmazonS3/latest/userguide/checking-object-integrity.html
- AWS Budgets pricing: https://aws.amazon.com/aws-cost-management/aws-budgets/pricing/
- AWS free tier (100 GB/month data transfer out): https://aws.amazon.com/free/
