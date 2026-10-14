"""The `filesystem` backend (store LLD § `filesystem` Backend).

An object is committed when `<dir>/.meta/<name>.json`, renamed into place after the object,
names the object's exact length and SHA-256. `head` hashes the object on every call, so any
crash state reads as either committed and correct or uncommitted.
"""

from __future__ import annotations

import contextlib
import errno
import hashlib
import json
import os
import secrets
import time
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Any, BinaryIO

from urbanbio.errors import IntegrityError, StorageError
from urbanbio.storage.store import MARKER_KEY, BaseStore, StoredObject

CHUNK = 1 << 20
DIR_MODE = 0o750
FILE_MODE = 0o640
ARCHIVE_FILE_MODE = 0o440
TRANSIENT_ERRNOS = frozenset(
    {errno.EIO, errno.ETIMEDOUT, errno.ESTALE, errno.EHOSTDOWN, errno.ENOTCONN}
)
RETRY_WAITS_S = (0.5, 1, 2)
META_DIR = ".meta"
TMP_MARK = ".tmp-"


def _hash_file(path: Path) -> tuple[int, str]:
    digest = hashlib.sha256()
    size = 0
    with open(path, "rb") as f:
        while chunk := f.read(CHUNK):
            digest.update(chunk)
            size += len(chunk)
    return size, digest.hexdigest()


def _fsync_dir(path: Path) -> None:
    fd = os.open(path, os.O_RDONLY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def _chunks(source: Path | bytes) -> Iterator[bytes]:
    if isinstance(source, bytes | bytearray):
        yield bytes(source)
        return
    with open(source, "rb") as f:
        while chunk := f.read(CHUNK):
            yield chunk


def _unlink_quietly(path: Path | None) -> None:
    if path is not None:
        with contextlib.suppress(FileNotFoundError):
            os.unlink(path)


class FilesystemStore(BaseStore):
    def __init__(self, root: Path) -> None:
        self._root = Path(root)

    @property
    def root(self) -> Path:
        return self._root

    def _object(self, key: str) -> Path:
        return self._root / key

    def _meta(self, key: str) -> Path:
        path = self._object(key)
        return path.parent / META_DIR / f"{path.name}.json"

    # --- Errors -----------------------------------------------------------------------------

    # @spec STORE-FS-011, STORE-API-016
    def _run(self, op: Callable[[], Any]) -> Any:
        for attempt in range(len(RETRY_WAITS_S) + 1):
            try:
                return op()
            except OSError as err:
                if err.errno not in TRANSIENT_ERRNOS:
                    raise
                if attempt == len(RETRY_WAITS_S):
                    raise StorageError(
                        f"the store filesystem isn't responding: {os.strerror(err.errno)}",
                        reason_code="store_unavailable",
                    ) from err
                time.sleep(RETRY_WAITS_S[attempt])
        raise AssertionError("unreachable")

    # --- Operations -------------------------------------------------------------------------

    # @spec STORE-FS-003
    def _head(self, key: str) -> tuple[StoredObject, StoredObject] | None:
        try:
            meta = json.loads(self._meta(key).read_text())
            size, sha256 = _hash_file(self._object(key))
        except (FileNotFoundError, NotADirectoryError, json.JSONDecodeError):
            return None
        if not isinstance(meta, dict) or (meta.get("size"), meta.get("sha256")) != (size, sha256):
            return None
        obj = StoredObject(key, size, sha256, dict(meta.get("metadata") or {}))
        return obj, obj

    def _make_dirs(self, directory: Path) -> None:
        missing = []
        for path in (directory, *directory.parents):
            if path == self._root or path.is_dir():
                break
            missing.append(path)
        for path in reversed(missing):
            try:
                os.mkdir(path, DIR_MODE)
            except FileExistsError:
                continue
            os.chmod(path, DIR_MODE)

    def _remove_stale_temporaries(self, key: str) -> None:
        final, meta = self._object(key), self._meta(key)
        for directory, stem in ((final.parent, final.name), (meta.parent, meta.name)):
            for stale in directory.glob(f".{stem}{TMP_MARK}*"):
                _unlink_quietly(stale)

    def _write_temp(self, final: Path, chunks: Iterator[bytes], mode: int) -> tuple[Path, int, str]:
        tmp = final.parent / f".{final.name}{TMP_MARK}{secrets.token_hex(6)}"
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        digest = hashlib.sha256()
        size = 0
        try:
            with os.fdopen(fd, "wb") as f:
                out: BinaryIO = f
                for chunk in chunks:
                    digest.update(chunk)
                    size += len(chunk)
                    out.write(chunk)
                out.flush()
                os.fsync(out.fileno())
                os.fchmod(out.fileno(), mode)
        except BaseException:
            _unlink_quietly(tmp)
            raise
        return tmp, size, digest.hexdigest()

    # @spec STORE-FS-001, STORE-FS-002, STORE-FS-007, STORE-FS-008, STORE-FS-010
    # @spec STORE-API-005, STORE-API-009
    def _write(
        self,
        key: str,
        source: Path | bytes,
        sha256: str,
        metadata: dict[str, str],
        expect: StoredObject | None,
    ) -> StoredObject:
        final, meta = self._object(key), self._meta(key)
        self._make_dirs(final.parent)
        self._make_dirs(meta.parent)
        self._remove_stale_temporaries(key)
        if expect is None and self._head(key) is not None:
            raise StorageError(
                f"an object already exists at {key}", reason_code="store_object_exists"
            )

        mode = ARCHIVE_FILE_MODE if key.startswith("archive/") else FILE_MODE
        obj_tmp: Path | None = None
        meta_tmp: Path | None = None
        try:
            obj_tmp, size, written = self._write_temp(final, _chunks(source), mode)
            if written != sha256:
                raise IntegrityError(
                    f"the content written to {key} doesn't match the given SHA-256",
                    reason_code="store_checksum_mismatch",
                )
            record = {"key": key, "size": size, "sha256": sha256, "metadata": metadata}
            meta_tmp, _, _ = self._write_temp(
                meta, iter([json.dumps(record, sort_keys=True).encode()]), FILE_MODE
            )
            if expect is not None:
                current = self._head(key)
                if current is None or current[1] != expect:
                    raise StorageError(
                        f"{key} changed since it was read", reason_code="store_object_changed"
                    )
            os.replace(obj_tmp, final)
            obj_tmp = None
            os.replace(meta_tmp, meta)
            meta_tmp = None
            _fsync_dir(final.parent)
            _fsync_dir(meta.parent)
        finally:
            _unlink_quietly(obj_tmp)
            _unlink_quietly(meta_tmp)

        if _hash_file(final) != (size, sha256):
            raise IntegrityError(
                f"reading back {key} gave a different length or SHA-256",
                reason_code="store_checksum_mismatch",
            )
        return StoredObject(key, size, sha256, dict(metadata))

    # @spec STORE-FS-004
    def _get(self, key: str, dest: Path) -> StoredObject:
        found = self._head(key)
        if found is None:
            raise StorageError(
                f"no committed object at {key}", reason_code="store_object_uncommitted"
            )
        obj = found[0]
        digest = hashlib.sha256()
        try:
            with open(dest, "wb") as out:
                for chunk in _chunks(self._object(key)):
                    digest.update(chunk)
                    out.write(chunk)
            if digest.hexdigest() != obj.sha256:
                raise IntegrityError(
                    f"{key} changed while it was read", reason_code="store_checksum_mismatch"
                )
        except BaseException:
            dest.unlink(missing_ok=True)
            raise
        return obj

    # @spec STORE-FS-005
    def _list(self, prefix: str) -> Iterator[str]:
        base = self._root / prefix.rpartition("/")[0] if "/" in prefix else self._root
        keys = []
        for dirpath, dirnames, filenames in os.walk(base):
            dirnames[:] = [d for d in dirnames if not d.startswith(".")]
            directory = Path(dirpath)
            for name in filenames:
                rel = (directory / name).relative_to(self._root).as_posix()
                if name.startswith(".") and rel != MARKER_KEY:
                    continue
                if rel.startswith(prefix) and self._meta(rel).is_file():
                    keys.append(rel)
        return keys

    # @spec STORE-FS-006
    def _delete(self, key: str) -> None:
        final = self._object(key)
        _unlink_quietly(self._meta(key))
        _unlink_quietly(final)
        if final.parent.is_dir():
            _fsync_dir(final.parent)

    def read_bytes(self, key: str) -> bytes | None:
        def op() -> bytes | None:
            found = self._head(key)
            return None if found is None else self._object(key).read_bytes()

        return self._run(op)

    # --- DuckDB and filesystems -------------------------------------------------------------

    # @spec STORE-FS-009
    def table_glob(self, prefix: str) -> str:
        return f"{self._root}/{prefix}**/*.parquet"

    # @spec STORE-FS-009
    def configure_duckdb(self, conn: Any) -> None:
        return None

    # @spec STORE-SAME-001
    def same_filesystem_as(self, path: Path) -> bool:
        return _device(Path(path)) == _device(self._root)


def _device(path: Path) -> int:
    """The device of `path`, or of its nearest existing ancestor."""
    path = path.resolve()
    for candidate in (path, *path.parents):
        try:
            return candidate.stat().st_dev
        except FileNotFoundError:
            continue
    raise FileNotFoundError(path)
