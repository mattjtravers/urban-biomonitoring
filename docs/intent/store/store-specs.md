# Store Specs

EARS requirements for [store](store-design.md). Markers: `[x]` implemented, `[ ]` active gap,
`[D]` deferred.

## Configuration

- [ ] **STORE-CFG-001**: The system shall select the store backend from `[store] backend`, accepting only `s3` or `filesystem`, and shall raise a `ConfigError` for any other value.
- [ ] **STORE-CFG-002**: If the `[store]` table sets a key that belongs only to the backend not selected (`region` or `glacier_transition_days` with `filesystem`; `root` with `s3`), then the system shall raise a `ConfigError`.
- [ ] **STORE-CFG-003**: Where the `s3` backend is selected, the system shall read the bucket name only from the `URBANBIO_BUCKET` environment variable.
- [ ] **STORE-CFG-004**: Where the `filesystem` backend is selected, the system shall expand and resolve `[store] root`, and shall raise a `ConfigError` when it is unset or lies inside the repository working tree.
- [ ] **STORE-CFG-005**: The system shall exclude `[store] glacier_transition_days` from every stage's parameters and idempotency key, using it only in the `audio_file_tier` view.

## Store Interface

- [ ] **STORE-API-001**: The system shall access stored objects only through a `Store` returned by `open_store(config)`, and no module outside `urbanbio.storage` shall import a storage SDK (`boto3`, `botocore`) or format a store key.
- [ ] **STORE-API-002**: When `put` is called on any backend, the system shall write the whole object at `key` and return only after confirming in the store that the object's length equals the source's and its SHA-256 equals the `sha256` argument.
- [ ] **STORE-API-003**: If the length or SHA-256 confirmed by `put` differs from the source's, then the system shall raise `IntegrityError` (`store_checksum_mismatch`).
- [ ] **STORE-API-004**: When `put` is called with `overwrite=False` and a committed object exists at `key`, the system shall raise `StorageError` (`store_object_exists`) without writing.
- [ ] **STORE-API-005**: The system shall treat only a complete object whose length and SHA-256 are confirmed as existing at a key, so a partial or uncommitted object left by a crash doesn't block a `put` with `overwrite=False`.
- [ ] **STORE-API-006**: When `put` is called with `overwrite=True` for a key under `runs/`, the system shall replace the existing object.
- [ ] **STORE-API-007**: When `put` is called with `overwrite=True` for a key under `archive/`, the system shall replace the existing object only when the existing object's `mask-version` metadata is lower than the new object's, and only when the existing object is unchanged since the `head` that read its mask version.
- [ ] **STORE-API-008**: If `put` is called with `overwrite=True` for a key outside `runs/` and `archive/`, or for an `archive/` key whose existing `mask-version` is not lower than the new one, then the system shall raise `StorageError` (`store_overwrite_forbidden`) before writing.
- [ ] **STORE-API-009**: If an allowed `archive/` overwrite finds the existing object changed since its `head`, then the system shall raise `StorageError` (`store_object_changed`) without replacing it.
- [ ] **STORE-API-010**: If `put` metadata has a non-ASCII key or value, a key that isn't lowercase letters, digits, and hyphens, or a total size over 2 KB, then the system shall raise `ValueError` before writing.
- [ ] **STORE-API-011**: When `head` is called, the system shall return the committed object's key, length, SHA-256, and metadata, or `None` when no committed object exists at the key.
- [ ] **STORE-API-012**: When `get` is called, the system shall write the object to `dest` and verify its SHA-256 against the stored value, raising `IntegrityError` (`store_checksum_mismatch`) on a mismatch.
- [ ] **STORE-API-013**: When `list` is called, the system shall yield the keys of committed objects under `prefix` in lexicographic order.
- [ ] **STORE-API-014**: If `delete` is called for a key outside `clips/`, then the system shall raise `StorageError` (`store_delete_forbidden`) before calling the backend.
- [ ] **STORE-API-015**: If a key passed to any `Store` method has a leading `/`, a `.`, `..`, or empty segment, or a character outside `[A-Za-z0-9._=-]` and `/`, then the system shall raise `ValueError`.
- [ ] **STORE-API-016**: If a backend operation still fails with a transient error (network, unresponsive share) after the backend's retries, then the system shall raise `StorageError` (`store_unavailable`).

## Key Layout

- [ ] **STORE-KEY-001**: The system shall build every store key in `urbanbio.storage.paths`, using the same layout on every backend.
- [ ] **STORE-KEY-002**: The system shall store archived audio at `archive/site=<site_id>/deployment=<deployment_id>/date=<YYYY-MM-DD>/<audio_file_id>.flac` and its header sidecar at the same prefix as `<audio_file_id>.header.json`, where `date` is the UTC date of the file start.
- [ ] **STORE-KEY-003**: The system shall store each ingest part's manifest at `retrievals/site=<site_id>/deployment=<deployment_id>/<retrieval_id>/manifest-part<NN>.json`, where `<NN>` is the two-digit part number.
- [ ] **STORE-KEY-004**: The system shall store each device file (logs, diagnostics, unknown files) at `retrievals/site=<site_id>/deployment=<deployment_id>/<retrieval_id>/device/part<NN>/<device path>`, where `<device path>` is the file's path relative to the part's card root with each character outside `[A-Za-z0-9._=-]` and each segment's leading `.` replaced by `_`.
- [ ] **STORE-KEY-005**: When two device paths in one ingest part sanitize to the same key (STORE-KEY-004), the system shall insert `-<8 hex>` before each such file's extension, the first 8 hex characters of the SHA-256 of its original path, and the part's manifest shall record each device file's original path with its key.
- [ ] **STORE-KEY-006**: The system shall store curated tables under `curated/<table>/<partitions>/<file>.parquet`, clips at `clips/<detection_id>.flac`, run records at `runs/<run_id>.json`, and run logs at `runs/<run_id>.log`.
- [ ] **STORE-KEY-007**: The system shall record store locations in run records, logs, and table rows only as store-relative keys, never with the bucket name or the `filesystem` root path.

## Store Marker

- [ ] **STORE-MARK-001**: When `urbanbio store init` runs, the system shall write `.urbanbio-store.json` at the store root through `put`, holding `schema` (`urbanbio.store-marker/1`), a random `store_id` (`st-` and 16 hex characters), `profile` (the current `URBANBIO_PROFILE`), and `created_utc`.
- [ ] **STORE-MARK-002**: If `urbanbio store init` finds a store marker of any profile, or any other object in the store, then the system shall refuse without writing.
- [ ] **STORE-MARK-003**: Where the `filesystem` backend is selected, when `urbanbio store init` runs and `[store] root` doesn't exist as a directory, the system shall refuse without creating it.
- [ ] **STORE-MARK-004**: While the profile is `processing`, when `urbanbio store init` runs on the `filesystem` backend and `[store] root` is on the same filesystem as `paths.quarantine` (STORE-SAME-001), the system shall refuse without writing.
- [ ] **STORE-MARK-005**: When `open_store` is called, the system shall read the store marker before any other store access.
- [ ] **STORE-MARK-006**: If `open_store` finds no store marker, then the system shall raise `ConfigError` (`store_marker_missing`) with a message naming `urbanbio store init` and, on the `filesystem` backend, saying that an unmounted share shows as an empty directory.
- [ ] **STORE-MARK-007**: If the store marker's `profile` differs from `URBANBIO_PROFILE`, then `open_store` shall raise `ConfigError` (`store_profile_mismatch`).
- [ ] **STORE-MARK-008**: When `urbanbio config check` runs, the system shall open the store through `open_store`, read only the `current_deployments` view, and write nothing to the store.

## `s3` Backend

- [ ] **STORE-S3-001**: Where the `s3` backend is selected, the system shall build its AWS S3 client from `AWS_ACCESS_KEY_ID`, `AWS_SECRET_ACCESS_KEY`, and `[store] region`, passed explicitly, and shall never read the AWS shared credentials or config files.
- [ ] **STORE-S3-002**: Where the `s3` backend is selected, the system shall retry AWS S3 requests with botocore's `standard` retry mode and 5 attempts.
- [ ] **STORE-S3-003**: When `put` runs on the `s3` backend, the system shall write the object in a single `PutObject` with `ChecksumAlgorithm=SHA256`, the precomputed `ChecksumSHA256`, and the metadata, then confirm it with `HeadObject(ChecksumMode="ENABLED")`, requiring the returned checksum and length to match.
- [ ] **STORE-S3-004**: When `put` runs on the `s3` backend with `overwrite=False`, the system shall send `If-None-Match: *`, and shall raise `StorageError` (`store_object_exists`) when the precondition fails.
- [ ] **STORE-S3-005**: When an allowed overwrite runs on the `s3` backend, the system shall send `If-Match` with the ETag from the preceding `head`, and shall raise `StorageError` (`store_object_changed`) when the precondition fails.
- [ ] **STORE-S3-006**: When `head` runs on the `s3` backend, the system shall call `HeadObject(ChecksumMode="ENABLED")` and return `None` on a 404.
- [ ] **STORE-S3-007**: When `get` runs on the `s3` backend, the system shall stream `GetObject` to `dest` with `ChecksumMode="ENABLED"`.
- [ ] **STORE-S3-008**: When `list` runs on the `s3` backend, the system shall page through `ListObjectsV2`.
- [ ] **STORE-S3-009**: Where the `s3` backend is selected, `table_glob(prefix)` shall return `s3://<bucket>/<prefix>**/*.parquet`, and `configure_duckdb` shall load `httpfs` and create a DuckDB secret of type `s3` with `KEY_ID`, `SECRET`, and `REGION` from the same values as the client.

## `filesystem` Backend

- [ ] **STORE-FS-001**: When `put` runs on the `filesystem` backend, the system shall create the key's parent directories, write the source to a temporary file `.<name>.tmp-<random>` in the target directory while hashing it, `fsync` it, set its mode, write the metadata (key, length, SHA-256, metadata map) to a temporary file in `<dir>/.meta/` and `fsync` it, rename the object to its final name, then rename the metadata to `.meta/<name>.json`, and `fsync` both directories.
- [ ] **STORE-FS-002**: When `put` on the `filesystem` backend has renamed the object and its metadata, the system shall re-open the final file, hash it in full, and require its length and SHA-256 to match the metadata.
- [ ] **STORE-FS-003**: When `head` runs on the `filesystem` backend, the system shall read `.meta/<name>.json`, `stat` the object, and hash it in full, returning the object only when it exists and its length and SHA-256 equal the metadata's, and returning `None` (uncommitted) otherwise.
- [ ] **STORE-FS-004**: If `get` on the `filesystem` backend finds the object uncommitted, then the system shall raise `StorageError` (`store_object_uncommitted`).
- [ ] **STORE-FS-005**: When `list` runs on the `filesystem` backend, the system shall skip `.meta/` directories and temporary files and yield only keys whose metadata file exists.
- [ ] **STORE-FS-006**: When `delete` runs on the `filesystem` backend, the system shall remove the metadata file before unlinking the object.
- [ ] **STORE-FS-007**: When the `filesystem` backend creates directories, it shall use mode `0750`; it shall create files with mode `0640`, and set files under `archive/` to `0440` before renaming them into place.
- [ ] **STORE-FS-008**: When `put` runs on the `filesystem` backend, the system shall remove temporary files left at the same key by an earlier crashed `put`.
- [ ] **STORE-FS-009**: Where the `filesystem` backend is selected, `table_glob(prefix)` shall return `<root>/<prefix>**/*.parquet`, and `configure_duckdb` shall do nothing.
- [ ] **STORE-FS-010**: When an allowed overwrite runs on the `filesystem` backend, the system shall repeat the `head` just before renaming the new object into place, and shall raise `StorageError` (`store_object_changed`) without renaming when the result differs from the earlier `head`.

## Same-Filesystem Rule

- [ ] **STORE-SAME-001**: When `store.same_filesystem_as(path)` is called, the system shall return whether the resolved path and the resolved `filesystem` store root have the same `st_dev`, and shall return false on the `s3` backend.

## Changing Stores

- [ ] **STORE-SWITCH-001**: When `open_store` reads the store marker, the system shall compare its `store_id` with `<paths.state>/store-id`, and shall write the `store_id` there when the file is absent.
- [ ] **STORE-SWITCH-002**: If `open_store` finds a `store_id` different from `<paths.state>/store-id` while the quarantine holds any `<retrieval_id>/` directory, then the system shall raise `ConfigError` (`store_changed`) naming both `store_id` values and those retrievals, deciding from the quarantine alone.
- [ ] **STORE-SWITCH-003**: When `open_store` finds a `store_id` different from `<paths.state>/store-id` and the quarantine holds no `<retrieval_id>/` directory, the system shall log a warning naming both `store_id` values, update `<paths.state>/store-id`, and continue.

## Deferred

- [D] **STORE-LOCK-001**: Where more than one processing host shares a store, the system shall hold a cross-host lock stored in the store while a command writes runs.
- [D] **STORE-MIGRATE-001**: When `urbanbio store migrate` runs, the system shall copy every object to the target backend, verify each copy's SHA-256, and write the target's marker with the source's `store_id`.
