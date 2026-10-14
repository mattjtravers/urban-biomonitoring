"""The `Store` interface, its contract rules, and `open_store` (store LLD § Store Interface).

`BaseStore` enforces the rules every backend shares (key and metadata shape, the overwrite and
delete policy) and leaves the storage operations to the backend. Stages get a store only from
`open_store`.
"""

from __future__ import annotations

import os
import re
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any, Protocol

from urbanbio.errors import ConfigError, StorageError

if TYPE_CHECKING:
    from urbanbio.config import PipelineConfig

MARKER_KEY = ".urbanbio-store.json"
METADATA_LIMIT_BYTES = 2048
OVERWRITABLE_PREFIXES = ("runs/", "archive/")
DELETABLE_PREFIX = "clips/"

_SEGMENT = re.compile(r"[A-Za-z0-9=_-][A-Za-z0-9._=-]*")
_METADATA_KEY = re.compile(r"[a-z0-9-]+")
_SHA256_HEX = re.compile(r"[0-9a-f]{64}")
_DECIMAL = re.compile(r"[0-9]+")


@dataclass(frozen=True)
class StoredObject:
    key: str
    size: int
    sha256: str  # hex, full-object
    metadata: dict[str, str]


class Store(Protocol):
    def put(
        self,
        key: str,
        source: Path | bytes,
        *,
        sha256: str,
        metadata: dict[str, str] | None = None,
        overwrite: bool = False,
    ) -> StoredObject: ...
    def head(self, key: str) -> StoredObject | None: ...
    def get(self, key: str, dest: Path) -> StoredObject: ...
    def list(self, prefix: str) -> Iterator[str]: ...
    def delete(self, key: str) -> None: ...
    def table_glob(self, prefix: str) -> str: ...
    def configure_duckdb(self, conn: Any) -> None: ...
    def same_filesystem_as(self, path: Path) -> bool: ...


# @spec STORE-API-015
def check_key(key: str) -> str:
    """Return `key` if it is a valid store key; raise ValueError otherwise."""
    if key == MARKER_KEY:
        return key
    if not isinstance(key, str) or not all(_SEGMENT.fullmatch(seg) for seg in key.split("/")):
        raise ValueError(f"not a valid store key: {key!r}")
    return key


# @spec STORE-API-010
def check_metadata(metadata: dict[str, str] | None) -> dict[str, str]:
    metadata = dict(metadata or {})
    total = 0
    for name, value in metadata.items():
        if not (isinstance(name, str) and isinstance(value, str)):
            raise ValueError("metadata keys and values must be strings")
        if not (name.isascii() and value.isascii()):
            raise ValueError("metadata keys and values must be ASCII")
        if not _METADATA_KEY.fullmatch(name):
            raise ValueError(f"metadata key isn't lowercase letters, digits, and hyphens: {name!r}")
        total += len(name) + len(value)
    if total > METADATA_LIMIT_BYTES:
        raise ValueError(f"metadata is {total} bytes, over the {METADATA_LIMIT_BYTES}-byte limit")
    return metadata


def mask_version(obj_metadata: dict[str, str]) -> int | None:
    value = obj_metadata.get("mask-version")
    return int(value) if value is not None and _DECIMAL.fullmatch(value) else None


class BaseStore:
    """Contract rules shared by every backend. Backends implement the underscored methods.

    `_head` returns the committed object with a backend token that identifies its exact
    version (an ETag, or the object itself), or None. `_write` writes and confirms an object;
    with `expect` None it must refuse an existing object, otherwise it must refuse when the
    object's current token differs from `expect`.
    """

    # --- Contract ---------------------------------------------------------------------------

    # @spec STORE-API-002, STORE-API-003, STORE-API-004, STORE-API-006, STORE-API-007
    # @spec STORE-API-008, STORE-API-017
    def put(
        self,
        key: str,
        source: Path | bytes,
        *,
        sha256: str,
        metadata: dict[str, str] | None = None,
        overwrite: bool = False,
    ) -> StoredObject:
        check_key(key)
        metadata = check_metadata(metadata)
        if not (isinstance(sha256, str) and _SHA256_HEX.fullmatch(sha256)):
            raise ValueError("sha256 must be 64 lowercase hex characters")
        if not overwrite:
            return self._run(lambda: self._write(key, source, sha256, metadata, expect=None))
        if not key.startswith(OVERWRITABLE_PREFIXES):
            raise StorageError(
                f"overwrite isn't allowed for {key}", reason_code="store_overwrite_forbidden"
            )

        def overwrite_op() -> StoredObject:
            found = self._head(key)
            if found is None:
                return self._write(key, source, sha256, metadata, expect=None)
            existing, token = found
            if key.startswith("archive/"):
                old, new = mask_version(existing.metadata), mask_version(metadata)
                if old is None or new is None or old >= new:
                    raise StorageError(
                        f"overwriting {key} needs a higher integer mask-version",
                        reason_code="store_overwrite_forbidden",
                    )
            return self._write(key, source, sha256, metadata, expect=token)

        return self._run(overwrite_op)

    # @spec STORE-API-011
    def head(self, key: str) -> StoredObject | None:
        check_key(key)
        found = self._run(lambda: self._head(key))
        return None if found is None else found[0]

    # @spec STORE-API-012
    def get(self, key: str, dest: Path) -> StoredObject:
        check_key(key)
        return self._run(lambda: self._get(key, Path(dest)))

    # @spec STORE-API-013
    def list(self, prefix: str) -> Iterator[str]:
        return iter(self._run(lambda: sorted(self._list(prefix))))

    # @spec STORE-API-014
    def delete(self, key: str) -> None:
        check_key(key)
        if not key.startswith(DELETABLE_PREFIX):
            raise StorageError(
                f"deleting {key} isn't allowed", reason_code="store_delete_forbidden"
            )
        self._run(lambda: self._delete(key))

    # --- Backend hooks ----------------------------------------------------------------------

    def _run(self, op: Any) -> Any:
        """Run one operation with the backend's retries and error mapping (STORE-API-016)."""
        return op()

    def _head(self, key: str) -> tuple[StoredObject, Any] | None:
        raise NotImplementedError

    def _write(
        self, key: str, source: Path | bytes, sha256: str, metadata: dict[str, str], expect: Any
    ) -> StoredObject:
        raise NotImplementedError

    def _get(self, key: str, dest: Path) -> StoredObject:
        raise NotImplementedError

    def _list(self, prefix: str) -> Iterator[str]:
        raise NotImplementedError

    def _delete(self, key: str) -> None:
        raise NotImplementedError

    def read_bytes(self, key: str) -> bytes | None:
        """The committed object's content, or None. For small objects such as the marker."""
        raise NotImplementedError


def source_size(source: Path | bytes) -> int:
    return len(source) if isinstance(source, bytes | bytearray) else os.stat(source).st_size


def build_store(cfg: PipelineConfig) -> BaseStore:
    """The backend named in configuration, without reading the marker. Only `store init` and
    `open_store` call this."""
    if cfg.store.backend == "s3":
        from urbanbio.storage.s3 import S3Store

        return S3Store.from_environment(cfg.store)
    from urbanbio.storage.filesystem import FilesystemStore

    if cfg.store.root is None:  # StoreConfig refuses this; kept for type narrowing.
        raise ConfigError(
            "the filesystem backend requires store.root", reason_code="config_invalid"
        )
    return FilesystemStore(cfg.store.root)


# @spec STORE-API-001, STORE-MARK-005
def open_store(cfg: PipelineConfig) -> BaseStore:
    """Build the configured backend, check its marker and the recorded store ID, and return it."""
    from urbanbio.storage.marker import check_marker

    store = build_store(cfg)
    check_marker(store, cfg)
    return store
