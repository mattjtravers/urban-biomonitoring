---
parent: high-level-design
prefix: STORE
---

# Store

## Context and Design Philosophy

The store is the pipeline's system of record (HLD §2, §5.5). It holds the archive, the device
logs, the curated tables, the clips, and the run records under one layout (HLD §5.3), on a
backend chosen in configuration. This LLD defines the store interface every stage uses, the key
layout, the store marker, and the two backends: `s3` (the default) and `filesystem`.

Four principles drive it:

- **Stages never address storage directly.** Every read and write goes through a `Store`
  object, and every key is built by `urbanbio.storage.paths`. A stage doesn't know which
  backend it runs on, and a new backend changes no stage.
- **A write is done when the store, not the writer, confirms it.** `put` returns only after the
  backend has confirmed the object's full SHA-256 in the store. The archive purges unmasked
  originals on the strength of that confirmation ([archive](../archive/archive-design.md)
  § Archive Flow).
- **The pipeline can't destroy the archive.** Deletes are allowed only under `clips/`, enforced
  in the interface on every backend and, on `s3`, also by IAM.
- **Refuse rather than write to the wrong place.** Every command that touches the store first
  reads the store marker and refuses to run when it is missing or belongs to another profile
  (HLD Tenet 3).

Package: `urbanbio.storage` (`store.py` interface and factory, `s3.py`, `filesystem.py`,
`paths.py`, `marker.py`, `tables.py`).

## Configuration

The backend is chosen by the `[store]` table of the profile's configuration: the committed
`config/<profile>.toml`, optionally overridden by the operator config file
([config-cli](../config-cli/config-cli-design.md) § Configuration).

```toml
[store]
backend = "s3"                     # "s3" | "filesystem"
region = "us-east-1"               # s3 only
# root = "/mnt/lab-share/urbanbio" # filesystem only; set in the operator config file
```

| Key | Backend | Default | Notes |
|---|---|---|---|
| `backend` | both | `s3` in `processing`, `filesystem` in `dev` | Any other value is a `ConfigError` |
| `region` | `s3` | `us-east-1` | AWS region of the bucket |
| `root` | `filesystem` | none in `processing`; `~/urbanbio-dev/store` in `dev` | Expanded and resolved; must lie outside the repository working tree |

A key that belongs to the other backend is a `ConfigError`, so a configuration can't look as if
it uses a store it doesn't. The `s3` bucket name is private and comes from `URBANBIO_BUCKET`; the
`filesystem` root is public configuration, kept in the operator config file when it names an
institution's share.

## Store Interface

```python
@dataclass(frozen=True)
class StoredObject:
    key: str
    size: int
    sha256: str                    # hex, full-object
    metadata: dict[str, str]

class Store(Protocol):
    def put(self, key: str, source: Path | bytes, *, sha256: str,
            metadata: dict[str, str] | None = None,
            overwrite: bool = False) -> StoredObject: ...
    def head(self, key: str) -> StoredObject | None: ...
    def get(self, key: str, dest: Path) -> StoredObject: ...
    def list(self, prefix: str) -> Iterator[str]: ...
    def delete(self, key: str) -> None: ...
    def table_glob(self, prefix: str) -> str: ...           # for DuckDB read_parquet
    def configure_duckdb(self, conn: DuckDBPyConnection) -> None: ...
```

`open_store(config)` builds the backend named in configuration, reads the marker, and returns a
`Store`. It is the only constructor stages use.

Contract, on every backend:

- **`put`** writes the whole object at `key`. Before returning, it confirms in the store that the
  object's length equals the source's and its SHA-256 equals `sha256`; a mismatch raises
  `IntegrityError` (`store_checksum_mismatch`). When an object already exists at `key` and
  `overwrite` is false, it raises `StorageError` (`store_object_exists`) without writing. Only
  a complete, verified object counts as existing; a partial write left by a crash does not.
  Metadata keys and values are ASCII, keys lowercase with hyphens, at most 2 KB in total.
- **`head`** returns the object's length, SHA-256, and metadata, or `None` when no object
  exists.
- **`get`** writes the object to `dest` and verifies its SHA-256 against the stored value.
- **`list`** yields keys under `prefix` in lexicographic order.
- **`delete`** removes the object at `key`. A key outside `clips/` raises `StorageError`
  (`store_delete_forbidden`) before the backend is called.
- **Keys** are relative POSIX paths built by `urbanbio.storage.paths`: no leading `/`, no `.` or
  `..` segment, no empty segment, and only `[A-Za-z0-9._=-]` and `/`. Any other key raises
  `ValueError`.
- **Transient failures** (network, a share that stops responding) raise `StorageError`
  (`store_unavailable`) after the backend's retries.

A single contract test suite, parametrized over both backends (`s3` against `moto`,
`filesystem` against a temporary directory), exercises every rule above. A new backend passes
the same suite.

## Key Layout

All keys are built by `urbanbio.storage.paths`; no other code formats keys. The layout is the
same on every backend: `s3://<bucket>/<key>` on `s3`, `<root>/<key>` on `filesystem`.

```
.urbanbio-store.json
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
appear in archive keys. A device file's original filename is sanitized to the key alphabet;
the original name is kept in the retrieval manifest.

## Store Marker

`.urbanbio-store.json` at the store root identifies the store:

```json
{
  "schema": "urbanbio.store-marker/1",
  "store_id": "st-7c2e9a41b0d35f68",
  "profile": "processing",
  "created_utc": "2026-10-12T14:05:00Z"
}
```

- **`urbanbio store init`** writes the marker for the current profile, with a random
  `store_id`. It refuses when a marker already exists (whatever its profile), and when the store
  already holds any object, so it never adopts a store it didn't create. On the `filesystem`
  backend it requires `root` to exist as a directory (it never creates the root, so it can't
  create a store inside an unmounted mount point) and, in the `processing` profile, refuses
  when `root` is on the same filesystem as `paths.quarantine` (compared by device ID; see
  Same-Filesystem Rule). An unmounted mount point is usually on the host's own disk, so this
  also catches a share that isn't mounted. The marker is written with `put`, so it is confirmed like any
  other object.
- **`open_store`** reads the marker before returning a store. A missing marker is a
  `ConfigError` (`store_marker_missing`), whose message names `urbanbio store init` and, on the
  `filesystem` backend, says that an unmounted share shows as an empty directory. A marker whose
  `profile` differs from `URBANBIO_PROFILE` is a `ConfigError` (`store_profile_mismatch`). Both
  stop the command before any other store access, and the run fails with exit `3`.
- **Run records** carry the `store_id`, so every output traces to the store that holds it
  ([runs](../runs/runs-design.md) § Run Record).
- **`urbanbio config check`** opens the store as above. It is the only store access `config
  check` makes.

The marker holds no private value; the `store_id` is random.

## `s3` Backend

The bucket is named by `URBANBIO_BUCKET`. The backend builds its boto3 client from
`AWS_ACCESS_KEY_ID`, `AWS_SECRET_ACCESS_KEY`, and `[store] region` read from configuration and
`os.environ` only: it passes credentials explicitly and never falls back to the AWS shared
credentials or config files (HLD §8). Retries use botocore's `standard` mode with 5 attempts.

| Operation | AWS S3 call |
|---|---|
| `put` | `PutObject` with `ChecksumAlgorithm = SHA256`, the precomputed `ChecksumSHA256`, and the metadata; then `HeadObject(ChecksumMode="ENABLED")`, requiring the returned checksum and length to match. With `overwrite=False`, the PUT carries `If-None-Match: *`, so an existing object fails the write (`store_object_exists`). Every object is a single PUT; objects are well under the 5 GB single-PUT limit. |
| `head` | `HeadObject(ChecksumMode="ENABLED")`; 404 gives `None` |
| `get` | `GetObject`, streamed to `dest` with `ChecksumMode="ENABLED"` |
| `list` | `ListObjectsV2` paginated |
| `delete` | `DeleteObject` |
| `table_glob` | `s3://<bucket>/<prefix>**/*.parquet` |
| `configure_duckdb` | Loads `httpfs` and creates a secret of type `s3` with `KEY_ID`, `SECRET`, and `REGION` from the same values as the client |

The bucket name never appears in logs, run records, or table rows: keys are stored relative to
the store root.

### Bucket configuration (runbook, AWS CLI)

Set up once by the operator with an administrator identity (HLD Tenet 4). The commands live in
`docs/runbooks/aws-setup.md` (written in the implementation phase) and do the following:

1. Create the bucket in the configured region.
2. **Block Public Access:** all four settings on, at bucket and account level.
3. **Object Ownership:** `BucketOwnerEnforced` (ACLs disabled).
4. **Default encryption:** SSE-S3 (AES-256).
5. **Versioning:** left disabled, so a re-masked file's previous version, which still contains
   the speech the new mask removes, doesn't survive ([archive](../archive/archive-design.md)
   § Re-masking).
6. **Bucket policy:** deny any request where `aws:SecureTransport` is `false`.
7. **Lifecycle configuration** (the tier and age are resolved in
   [archive](../archive/archive-design.md) § T1 resolution):

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

8. **AWS Budgets:** a monthly cost budget sized from [archive](../archive/archive-design.md)
   § Cost (USD 15 for the three-recorder volume model) with email alerts at 50%, 80%, and 100%
   of actual spend and 100% of forecast spend. The email address is entered in AWS only, never
   in the repository. Budgets without actions are free.
9. `urbanbio store init` with the pipeline key, to write the marker.

### IAM: pipeline principal

An IAM user `urbanbio-pipeline` whose access key is supplied to the pipeline through
`AWS_ACCESS_KEY_ID` and `AWS_SECRET_ACCESS_KEY`. How the key reaches the environment, and its
rotation schedule, are operator concerns documented in runbooks. The user has only this inline
policy (`<bucket>` substituted at setup):

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
        "arn:aws:s3:::<bucket>/.urbanbio-store.json",
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

## `filesystem` Backend

The store root is `[store] root`, a directory on any mounted filesystem: a lab NFS or SMB
share, an external disk, or a FUSE mount of object storage. The backend uses only operations
that local filesystems and NFS/SMB shares support (create, write, fsync, rename within a
directory, chmod, unlink). Durability, backups, and access control come from the filesystem the
operator provides.

| Operation | Behavior |
|---|---|
| `put` | Create the key's parent directories. Write the source to a temporary file `.<name>.tmp-<random>` in the target directory, hashing while writing; `fsync` it; set its mode. Write the metadata (key, length, SHA-256, metadata map) to a temporary file in `<dir>/.meta/` and `fsync` it. Then `rename` the object to its final name, `rename` the metadata to `.meta/<name>.json`, and `fsync` both directories. Finally re-open the final file, hash it in full, and require the length and SHA-256 to match the metadata. With `overwrite=False`, a committed object at the key raises `store_object_exists` before writing; an uncommitted one (below) is replaced. |
| `head` | Read `.meta/<name>.json`, `stat` the object, and hash it in full. When the object exists and its length and SHA-256 equal the metadata's, return it. When the object is missing, or the metadata is missing or disagrees, the object is **uncommitted** and `head` returns `None`. |
| `get` | `head`, then copy to `dest`, hashing, and compare with the committed SHA-256. An uncommitted object raises `StorageError` (`store_object_uncommitted`). |
| `list` | Walk the directory tree under `prefix`, skipping `.meta/` directories and temporary files, and yield keys whose metadata file exists |
| `delete` | Remove the metadata file, then `unlink` the object |
| `table_glob` | `<root>/<prefix>**/*.parquet` |
| `configure_duckdb` | No setup |

**Modes.** Directories are created with mode `0750` and files with `0640`, so the operator can
grant a lab group read access through the root's group (for example, a setgid root directory)
without the pipeline widening access itself. Files under `archive/` are set to `0440` before the
rename, so they can't be modified in place; a re-mask replaces the file by rename, which needs
only write access to the directory.

**Commit point.** An object is committed when its metadata file, renamed last, names the
object's exact length and SHA-256. A crash at any point in `put` leaves the key either committed
with the old content, uncommitted, or committed with the new content, never committed with a
mismatch. A crash between the two renames during an overwrite (a re-mask) leaves the new object
with the old metadata: the key is uncommitted, `head` returns `None`, `get` refuses it, and the
next `put` to the key replaces it. The previous object is already gone at that point, so a
crashed re-mask never leaves the earlier, less-masked audio behind. `head` hashes the object on
every call; archive objects are a few MB, so the cost is small next to encoding.

**Temporary files** left by a crash are ignored by `list` and `head`, and removed by the next
`put` to the same key.

**Direct reads by DuckDB.** `table_glob` lets DuckDB read Parquet files directly, without
`head`. Table files have deterministic names per run and unit
([data-model](../data-model/data-model-design.md) § Parquet Tables), so an uncommitted table file left
by a crash is the same file the re-run rewrites, and no row is counted twice.

**What the read-back proves.** The read-back confirms what the filesystem returns for the final
file. On a filesystem that caches writes locally before sending them on (some FUSE mounts and
sync clients), that may be the local cache rather than the remote copy. Choosing a filesystem
whose writes are durable when `fsync` returns is the operator's responsibility, and the
filesystem-backend runbook says so.

## Same-Filesystem Rule

`store.same_filesystem_as(path)` returns whether a local path is on the same filesystem as the
store root, comparing `st_dev` of the resolved root and the resolved path. It is always false on
`s3`. It is used by `store init` (above) and by card-wipe readiness
([intake](../ingest/intake/intake-design.md) § Card Wipe Readiness), so the archive is never only
on the disk that holds the working copies when a card is wiped.

## Changing Stores

Each host records the `store_id` it last opened in `<paths.state>/store-id`. When `open_store`
finds a different `store_id` (the operator changed `[store]` or `URBANBIO_BUCKET`, or the
store was replaced):

- if the quarantine holds any retrieval that isn't yet safe to wipe, the command exits with a
  `ConfigError` (`store_changed`). Those retrievals' ledger rows are in the previous store, so
  continuing against the new one would re-ingest them without their history. The message names
  both `store_id` values and the retrievals;
- otherwise it logs a warning naming both `store_id` values, updates `store-id`, and continues.

A new store starts with an empty ledger and an empty archive. Data in the previous store stays
there and isn't visible to the pipeline until a migration tool exists (Deferred).

## Concurrency

The pipeline assumes one processing host writes to a store at a time. The run lock is a local
file on that host ([runs](../runs/runs-design.md) § Concurrency), so it doesn't stop a second
host. Two hosts writing to one store don't corrupt it, because object names carry run IDs or
content IDs and `put` without `overwrite` never replaces an object, but they can repeat each
other's work.

## Decisions & Alternatives

| Decision | Chosen | Alternatives Considered | Rationale |
|---|---|---|---|
| Storage abstraction | A small `Store` interface with one class per backend | Pipeline sees only a directory, and AWS S3 is reached through a FUSE mount; fsspec URLs for every backend | A FUSE mount needs privileged system setup that many hosts and containers lack, restricts the whole pipeline to the mount's subset of file operations, and gives up checksum-verified uploads. fsspec's backends differ in conditional writes and checksums, which weakens the fidelity proof that gates purging originals. Two explicit backends keep each backend's guarantee visible and testable. |
| Default backend | `s3` | `filesystem` | Archive volume (about 360 GB per recorder-year) outgrows a typical processing host's disk within weeks. AWS S3 gives durable, cheap, tiered storage with no infrastructure to run. |
| Backend selection | `[store]` in the profile's config file, overridable in the operator config file | Environment variable; command-line flag | It's deployment configuration like paths and budgets, so it lives with them and is validated with them. |
| Store identity check | Marker file written by `store init`, read before any access | Infer production from the bucket name; trust configuration | A marker detects an unmounted share, a wrong bucket, and a dev profile pointed at production, on every backend, without guessing from names. |
| `s3` credentials | Explicit from environment variables only | The full boto3 default credential chain | Keeps one rule for every private value (HLD §8). The default chain would silently read shared credential files outside the operator's chosen source. |
| `s3` credential type | Long-lived IAM user key scoped to the pipeline's prefixes | IAM Identity Center or OIDC short-lived credentials | Short-lived credentials need an identity provider and a sign-in step in every session. A narrowly scoped key that can't delete the archive is the simplest workable option for a small deployment; how often it's rotated is the operator's call, documented in runbooks. |
| `s3` write checksum | SHA-256 additional checksum on single PUTs | MD5 `Content-MD5`; CRC64NVME multipart | SHA-256 is the project's identity hash, so one value serves provenance and transport integrity. Single PUTs keep it a full-object checksum. |
| `s3` existing-object guard | Conditional PUT (`If-None-Match: *`) | `HeadObject` then PUT | One request, and no race between the check and the write. |
| `filesystem` metadata | JSON file in a `.meta/` directory beside the object | Extended attributes; one metadata database per store | Extended attributes aren't preserved by many NFS/SMB setups and copy tools. A database file is a single point of corruption and needs locking on a shared filesystem. |
| `filesystem` write | Temporary file, fsync, rename, read-back | Write in place; copy then compare | Rename makes the object appear whole or not at all, so a crash never leaves a truncated object at a real key. |
| `filesystem` commit point | Metadata renamed after the object; `head` hashes the object | Metadata first (the object's existence as commit); `head` compares lengths only | With metadata first, a crash during a re-mask leaves new metadata beside the old object, and equal lengths would hide it. Renaming metadata last and hashing in `head` makes every crash state either committed-and-correct or visibly uncommitted, and a re-run repairs it. |
| Store switch guard | Refuse while retrievals are mid-flight; warn otherwise | Always refuse; never check | A switch with nothing in flight is a legitimate operator choice. A switch mid-retrieval separates a card's files from their ledger, which Tenet 3 says to stop on. |
| Archive immutability on `filesystem` | Read-only file modes plus interface-level delete restriction | Rely on the interface only | The mode also stops other tools on the host from editing archived audio in place. Full protection would need filesystem-level controls the pipeline can't assume. |
| One backend per store | A store lives on exactly one backend | Tiered mixes (recent on disk, old in AWS S3) | One root, one marker, one ledger. Mixing would make "where is this object" a lookup instead of a key. |

## Open Questions & Future Decisions

### Deferred
1. A cross-host lock stored in the store, if more than one processing host ever shares a store.
2. A `store migrate` command that copies a store to another backend, verifying every object,
   and writes the new marker with the same `store_id`.
3. A separate read-only `s3` key for dev-profile hosts that analyze curated tables.

## References

- HLD §2, §5.3, §5.5, §6.1, Tenets 3–5
- Checking object integrity (additional checksums):
  https://docs.aws.amazon.com/AmazonS3/latest/userguide/checking-object-integrity.html
- Conditional writes (`If-None-Match`):
  https://docs.aws.amazon.com/AmazonS3/latest/userguide/conditional-writes.html
- Lifecycle transition constraints:
  https://docs.aws.amazon.com/AmazonS3/latest/userguide/lifecycle-transition-general-considerations.html
- DuckDB S3 secrets: https://duckdb.org/docs/extensions/httpfs/s3api
- moto: https://docs.getmoto.org/
