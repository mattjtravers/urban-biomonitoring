---
parent: high-level-design
prefix: ARCH
---

# Archive and AWS S3 Storage

## Context and Design Philosophy

The archive is the pipeline's reprocessing source (HLD §5.2): every stage after the speech
screen can be re-run from it. It holds speech-masked audio as lossless FLAC, the verbatim
device header of every file, and the device logs, in one private AWS S3 bucket that is the
system of record (HLD §2, §5.1).

Three properties are non-negotiable:

1. **Fidelity.** Every sample outside the mask is bit-identical to the original, and that is
   checked before the original is deleted, not assumed.
2. **Only masked audio leaves the quarantine.** The upload API accepts only `MaskedAudio`
   ([speech-screen](../speech-screen/speech-screen-design.md) § Padding, Merging, and the
   Mask).
3. **Private storage.** The bucket blocks all public access. Nothing in it is ever made public.
   Published material is served from GitHub Pages ([publish](../publish/publish-design.md)).

Package: `urbanbio.storage` (`archive.py`, `flac.py`, `sidecar.py`, `s3.py`, `paths.py`).

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
6. **Check for an earlier upload.** `HeadObject` the target key. If an object exists with the
   same `mask-version` metadata:
   - same `masked-pcm-sha256`: the upload already happened (for example, the run crashed
     before its ledger flush). Skip the PUT and continue at step 7 with the existing object's
     checksum. Identical samples count as identical even when FLAC bytes differ (for example,
     after a libsndfile upgrade).
   - different `masked-pcm-sha256`: raise `IntegrityError` (`archive_conflict`) and flag. An
     archived version is never overwritten except by a higher `mask_version`.

   **Upload** the FLAC and then the sidecar with `PutObject`, each with `ChecksumAlgorithm =
   SHA256` and the precomputed `ChecksumSHA256`, so AWS S3 rejects a corrupted upload. Every
   object is a single PUT (audio files are a few MB, well under the 5 GB single-PUT limit).
   Object metadata: `audio-file-id`, `mask-version`, `masked-sha256`, `masked-pcm-sha256`,
   `original-sha256`.
7. **Confirm** with `HeadObject(ChecksumMode="ENABLED")`: the returned SHA-256 checksum and
   length must match.
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

**Device files.** The summary log, diagnostics, and unknown files from the card are uploaded
verbatim under the retrieval prefix (below), after a content check: any file whose header
identifies it as audio (RIFF/WAVE, FLAC, Ogg, MP3) and wasn't processed as an audio file is
flagged `unexpected_audio` and **not uploaded**.

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
  through steps 3–8 with `mask_version + 1` and **overwrites** the object at the same key. The
  sidecar is unchanged, and there's no original to purge.
- The bucket has versioning disabled, so the previous masked version, which contains speech the
  new mask removes, no longer exists anywhere (Tenet 1).
- A new AudioMaskVersion row records the version, checksums, events, and run.

## AWS S3 Layout

One private bucket in `us-east-1`. Its name comes from `URBANBIO_BUCKET`. All keys are built by
`urbanbio.storage.paths`; no other code formats keys.

```
archive/site=<site_id>/deployment=<deployment_id>/date=<YYYY-MM-DD>/<audio_file_id>.flac
archive/site=<site_id>/deployment=<deployment_id>/date=<YYYY-MM-DD>/<audio_file_id>.header.json
retrievals/site=<site_id>/deployment=<deployment_id>/<retrieval_id>/manifest.json
retrievals/site=<site_id>/deployment=<deployment_id>/<retrieval_id>/device/<original filename>
curated/<table>/<partitions>/<file>.parquet
clips/<detection_id>.flac
runs/<run_id>.json
runs/<run_id>.log
```

`date` is the UTC date of the file start. Filenames are opaque IDs, so recorder names don't
appear in archive keys.

## Bucket Configuration (runbook, AWS CLI)

Set up once by the maintainer with an administrator identity (Tenet 4). The commands live in
`docs/runbooks/aws-setup.md` (written in the implementation phase) and do the following:

1. Create the bucket in `us-east-1`.
2. **Block Public Access:** all four settings on, at bucket and account level.
3. **Object Ownership:** `BucketOwnerEnforced` (ACLs disabled).
4. **Default encryption:** SSE-S3 (AES-256).
5. **Versioning:** left disabled (see Re-masking).
6. **Bucket policy:** deny any request where `aws:SecureTransport` is `false`.
7. **Lifecycle configuration:**

```json
{
  "Rules": [
    {
      "ID": "archive-audio-to-glacier-ir",
      "Status": "Enabled",
      "Filter": {"And": {"Prefix": "archive/", "ObjectSizeGreaterThan": 131072}},
      "Transitions": [{"Days": 30, "StorageClass": "GLACIER_IR"}]
    },
    {
      "ID": "abort-incomplete-multipart-uploads",
      "Status": "Enabled",
      "Filter": {"Prefix": ""},
      "AbortIncompleteMultipartUpload": {"DaysAfterInitiation": 7}
    }
  ]
}
```

The size filter keeps sidecars (a few KB) in S3 Standard. AWS S3 doesn't transition objects
under 128 KB by default, and the per-object transition charge would exceed their storage
savings.

8. **AWS Budgets:** a monthly cost budget of USD 15 with email alerts at 50%, 80%, and 100% of
   actual spend and 100% of forecast spend. The email address is entered in AWS only, never in
   the repository. Budgets without actions are free.

## IAM: Pipeline Principal

An IAM user `urbanbio-pipeline` whose access key (`AWS_ACCESS_KEY_ID`,
`AWS_SECRET_ACCESS_KEY`) is stored only in the private-value sources outside the repository:
the processing machine's local env file and the Codespaces secrets
([config-cli](../config-cli/config-cli-design.md) § Private values). The runbook rotates the
key every 90 days and updates both sources and the maintainer's password manager. The
user has only this inline policy (`<bucket>` substituted at setup):

```json
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Sid": "ListBucket",
      "Effect": "Allow",
      "Action": "s3:ListBucket",
      "Resource": "arn:aws:s3:::<bucket>"
    },
    {
      "Sid": "ReadWritePipelineObjects",
      "Effect": "Allow",
      "Action": ["s3:GetObject", "s3:PutObject", "s3:AbortMultipartUpload"],
      "Resource": [
        "arn:aws:s3:::<bucket>/archive/*",
        "arn:aws:s3:::<bucket>/retrievals/*",
        "arn:aws:s3:::<bucket>/curated/*",
        "arn:aws:s3:::<bucket>/clips/*",
        "arn:aws:s3:::<bucket>/runs/*"
      ]
    },
    {
      "Sid": "DeleteClipsOnly",
      "Effect": "Allow",
      "Action": "s3:DeleteObject",
      "Resource": "arn:aws:s3:::<bucket>/clips/*"
    }
  ]
}
```

The pipeline can't delete archive, curated, or run objects, can't change bucket settings,
lifecycle, or policy, and has no access to other AWS services. `HeadObject` is authorized by
`s3:GetObject`. Re-masking overwrites with `s3:PutObject`. Deleting anything outside `clips/`
needs the administrator identity.

## Cost

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
100 GB/month (AWS free tier). The processing machine is outside AWS, so downloads from the
archive to it count as internet transfer out. Uploads into AWS S3 are free.

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

Processing runs on the maintainer's own processing machine, so it adds no compute charge. The
cost model has no compute line. The Codespaces dev container is used for development only, and
its use is outside this model.

The processing machine uploads over the maintainer's wired home internet connection, about
20 Mbit/s upstream. A weekly card pair is about 14 GB of FLAC (two recorders × 7 days × about
1.0 GB), about 1.5 hours of upload, overlapping the screen and detect compute. **TODO:
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
| Fidelity proof | Decode-and-compare before upload; checksum-verified PUT; HeadObject confirm | Trust the encoder; compare object size | Purge deletes the only unmasked copy, so fidelity must be proven, not assumed. |
| Bucket versioning | Disabled | Enabled with noncurrent-version expiry | A noncurrent version of a re-masked file still contains the speech the new mask removes (Tenet 1). Deletion protection comes from IAM: the pipeline can't delete. |
| Buckets | One private bucket, prefixes per zone | Separate buckets per zone | One policy, one lifecycle, one budget. The IAM policy scopes by prefix anyway. |
| Public material | GitHub Pages only | A public AWS S3 prefix | Keeps "nothing in the bucket is public" absolute and avoids public egress charges. |
| Archive tier (T1) | Glacier IR after 30 days | Standard-IA; Glacier Flexible Retrieval; Deep Archive | See T1 resolution. |
| Credentials | Long-lived IAM user key in the private-value sources (local env file, Codespaces secrets), rotated every 90 days | IAM Identity Center or OIDC short-lived credentials | Short-lived credentials need an identity provider and a sign-in step in every session, in both environments, which is a lot of setup for one maintainer. A narrowly scoped key that can't delete the archive, rotated on a schedule, is the simplest workable option. |
| Upload checksums | SHA-256 additional checksum on single PUTs | MD5 `Content-MD5`; CRC64NVME multipart | SHA-256 is the project's identity hash, so one value serves provenance and transport integrity. Single PUTs keep it a full-object checksum. |

## Open Questions & Future Decisions

### Deferred
1. FLAC compression ratio on real recordings (affects the cost model only).
2. Moving data older than two years to Deep Archive once the archive passes about 5 TB.
3. A separate read-only IAM key for the development environment, which no longer writes to
   the archive ([config-cli](../config-cli/config-cli-design.md) § Environments). Set up with
   the AWS setup runbook.
4. Cross-region replication or a second-account backup. Not planned: AWS S3 stores data
   redundantly across Availability Zones, and the cost would double.

## References

- HLD §2, §5, §6.1, T1, Tenets 1 and 4
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
