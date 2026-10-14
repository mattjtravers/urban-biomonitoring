"""The `filesystem` backend against a temporary directory (store LLD § `filesystem` Backend).

Crash states are made by failing one `rename` (or `unlink`) partway through an operation. Both
`os.rename` and `os.replace` are wrapped, so the tests don't depend on which one the backend
uses.
"""

from __future__ import annotations

import json
import os
import re
import stat
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest

from tests.conftest import Project, sha256_hex, store_init
from urbanbio import config
from urbanbio.errors import IntegrityError, StorageError
from urbanbio.storage.store import open_store

DATA = b"synthetic object body\n" * 64
OTHER = b"a different synthetic body\n" * 32
RUN = "runs/detect-20261012T140500Z-ab12.json"
CLIP = "clips/det-0123456789abcdef.flac"
ARCHIVE = "archive/site=site1/deployment=site1-dep001/date=2026-03-02/af-0123456789abcdef.flac"


class Crash(Exception):
    """Stands in for the process dying at one step of an operation."""


@pytest.fixture
def root(fs_env: Project) -> Path:
    return (fs_env.home / "urbanbio-dev" / "store").resolve()


@pytest.fixture
def fs_store(fs_env: Project) -> Any:
    result = store_init()
    assert result.exit_code == 0, result.output
    return open_store(config.load(with_sites=False))


def put(store: Any, key: str, data: bytes = DATA, **kwargs: Any) -> Any:
    return store.put(key, data, sha256=sha256_hex(data), **kwargs)


def object_path(root: Path, key: str) -> Path:
    return root / key


def meta_path(root: Path, key: str) -> Path:
    path = root / key
    return path.parent / ".meta" / f"{path.name}.json"


def wrap_renames(
    monkeypatch: pytest.MonkeyPatch, hook: Callable[[Path, Path, Callable[[], None]], None]
) -> list[tuple[Path, Path]]:
    """Route every rename through `hook(src, dst, do_rename)` and record it."""
    seen: list[tuple[Path, Path]] = []
    for name in ("rename", "replace"):
        real = getattr(os, name)

        def wrapped(src: Any, dst: Any, *args: Any, _real: Any = real, **kwargs: Any) -> None:
            s, d = Path(os.fsdecode(src)), Path(os.fsdecode(dst))
            seen.append((s, d))
            hook(s, d, lambda: _real(src, dst, *args, **kwargs))

        monkeypatch.setattr(os, name, wrapped)
    return seen


def crash_on_rename_to(monkeypatch: pytest.MonkeyPatch, target: Path) -> list[tuple[Path, Path]]:
    def hook(src: Path, dst: Path, do: Callable[[], None]) -> None:
        if dst == target:
            raise Crash(f"crashed renaming to {dst}")
        do()

    return wrap_renames(monkeypatch, hook)


def mode(path: Path) -> int:
    return stat.S_IMODE(path.stat().st_mode)


# --- put ------------------------------------------------------------------------------------


# @spec STORE-FS-001
def test_put_writes_the_object_and_its_metadata(fs_store: Any, root: Path) -> None:
    put(fs_store, CLIP, metadata={"mask-version": "1"})
    assert object_path(root, CLIP).read_bytes() == DATA
    meta = json.loads(meta_path(root, CLIP).read_text())
    values = list(meta.values())
    assert CLIP in values
    assert len(DATA) in values
    assert sha256_hex(DATA) in values
    assert {"mask-version": "1"} in values


# @spec STORE-FS-001
def test_put_renames_a_temporary_object_then_its_metadata(
    fs_store: Any, root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    seen = wrap_renames(monkeypatch, lambda src, dst, do: do())
    put(fs_store, CLIP)
    final, meta = object_path(root, CLIP), meta_path(root, CLIP)
    (obj_src, obj_dst), (meta_src, meta_dst) = [(s, d) for s, d in seen if d in (final, meta)]
    assert (obj_dst, meta_dst) == (final, meta)
    assert obj_src.parent == final.parent
    assert re.fullmatch(re.escape(f".{final.name}.tmp-") + r".+", obj_src.name)
    assert meta_src.parent == meta.parent


# @spec STORE-FS-001
def test_put_fsyncs_the_files_and_both_directories(
    fs_store: Any, root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    synced: list[os.stat_result] = []
    real = os.fsync

    def spy(fd: int) -> None:
        synced.append(os.fstat(fd))
        real(fd)

    monkeypatch.setattr(os, "fsync", spy)
    put(fs_store, CLIP)
    dirs = {(s.st_dev, s.st_ino) for s in synced if stat.S_ISDIR(s.st_mode)}
    for directory in (object_path(root, CLIP).parent, meta_path(root, CLIP).parent):
        st = directory.stat()
        assert (st.st_dev, st.st_ino) in dirs
    assert sum(stat.S_ISREG(s.st_mode) for s in synced) >= 2


# @spec STORE-FS-001
def test_a_crash_before_the_object_rename_commits_nothing(fs_store: Any, root: Path) -> None:
    with pytest.MonkeyPatch.context() as mp, pytest.raises(Crash):
        crash_on_rename_to(mp, object_path(root, CLIP))
        put(fs_store, CLIP)
    assert not object_path(root, CLIP).exists()
    assert fs_store.head(CLIP) is None
    assert list(fs_store.list("clips/")) == []


# @spec STORE-FS-001, STORE-FS-005
def test_a_crash_between_the_renames_leaves_the_key_uncommitted(fs_store: Any, root: Path) -> None:
    with pytest.MonkeyPatch.context() as mp, pytest.raises(Crash):
        crash_on_rename_to(mp, meta_path(root, CLIP))
        put(fs_store, CLIP)
    # The object was renamed first, so it is in place without its metadata.
    assert object_path(root, CLIP).read_bytes() == DATA
    assert fs_store.head(CLIP) is None
    assert list(fs_store.list("clips/")) == []


# @spec STORE-FS-001, STORE-FS-003
def test_a_crash_during_a_remask_never_leaves_the_old_audio_committed(
    fs_store: Any, root: Path, tmp_path: Path
) -> None:
    put(fs_store, ARCHIVE, metadata={"mask-version": "1"})
    with pytest.MonkeyPatch.context() as mp, pytest.raises(Crash):
        crash_on_rename_to(mp, meta_path(root, ARCHIVE))
        put(fs_store, ARCHIVE, OTHER, metadata={"mask-version": "2"}, overwrite=True)
    assert object_path(root, ARCHIVE).read_bytes() == OTHER
    assert fs_store.head(ARCHIVE) is None
    with pytest.raises(StorageError) as caught:
        fs_store.get(ARCHIVE, tmp_path / "out.flac")
    assert caught.value.reason_code == "store_object_uncommitted"
    # The next put to the key repairs it.
    put(fs_store, ARCHIVE, OTHER, metadata={"mask-version": "2"})
    assert fs_store.head(ARCHIVE).metadata == {"mask-version": "2"}


# @spec STORE-FS-002
def test_put_reads_back_the_final_file(
    fs_store: Any, root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    final = object_path(root, CLIP)

    def damage_after_rename(src: Path, dst: Path, do: Callable[[], None]) -> None:
        do()
        if dst == final:
            with final.open("r+b") as f:
                f.write(b"X")

    wrap_renames(monkeypatch, damage_after_rename)
    with pytest.raises(IntegrityError) as caught:
        put(fs_store, CLIP)
    assert caught.value.reason_code == "store_checksum_mismatch"


# @spec STORE-API-005
def test_an_object_without_metadata_doesnt_block_a_put(fs_store: Any, root: Path) -> None:
    final = object_path(root, CLIP)
    final.parent.mkdir(parents=True, exist_ok=True)
    final.write_bytes(OTHER)
    put(fs_store, CLIP)
    assert final.read_bytes() == DATA
    assert fs_store.head(CLIP).sha256 == sha256_hex(DATA)


# @spec STORE-API-005
def test_an_uncommitted_archive_object_doesnt_block_a_put(fs_store: Any, root: Path) -> None:
    put(fs_store, ARCHIVE, metadata={"mask-version": "1"})
    meta_path(root, ARCHIVE).unlink()
    put(fs_store, ARCHIVE, OTHER, metadata={"mask-version": "1"})
    assert object_path(root, ARCHIVE).read_bytes() == OTHER


# @spec STORE-FS-008
def test_put_removes_temporary_files_left_at_its_key(fs_store: Any, root: Path) -> None:
    final = object_path(root, CLIP)
    final.parent.mkdir(parents=True, exist_ok=True)
    stale = final.parent / f".{final.name}.tmp-stale01"
    stale.write_bytes(b"partial")
    other = final.parent / ".det-ffffffffffffffff.flac.tmp-stale02"
    other.write_bytes(b"partial")
    put(fs_store, CLIP)
    assert not stale.exists()
    assert other.exists()


# --- head and get ---------------------------------------------------------------------------


def _drop_metadata(root: Path, key: str) -> None:
    meta_path(root, key).unlink()


def _drop_object(root: Path, key: str) -> None:
    object_path(root, key).unlink()


def _flip_a_byte(root: Path, key: str) -> None:
    path = object_path(root, key)
    data = bytearray(path.read_bytes())
    data[0] ^= 0xFF
    path.write_bytes(bytes(data))


def _append(root: Path, key: str) -> None:
    with object_path(root, key).open("ab") as f:
        f.write(b"!")


# @spec STORE-FS-003
@pytest.mark.parametrize("damage", [_drop_metadata, _drop_object, _flip_a_byte, _append])
def test_head_returns_none_for_an_uncommitted_object(
    fs_store: Any, root: Path, damage: Callable[[Path, str], None]
) -> None:
    put(fs_store, RUN)
    damage(root, RUN)
    assert fs_store.head(RUN) is None


# @spec STORE-FS-004
def test_get_refuses_an_uncommitted_object(fs_store: Any, root: Path, tmp_path: Path) -> None:
    put(fs_store, RUN)
    _flip_a_byte(root, RUN)
    with pytest.raises(StorageError) as caught:
        fs_store.get(RUN, tmp_path / "out.json")
    assert caught.value.reason_code == "store_object_uncommitted"


# --- list and delete ------------------------------------------------------------------------


# @spec STORE-FS-005
def test_list_skips_metadata_temporary_and_uncommitted_files(fs_store: Any, root: Path) -> None:
    put(fs_store, RUN)
    runs = root / "runs"
    (runs / ".detect-20261012T140500Z-ffff.json.tmp-abc123").write_bytes(b"partial")
    (runs / "orphan.json").write_bytes(b"no metadata")
    assert list(fs_store.list("runs/")) == [RUN]


# @spec STORE-FS-006
def test_delete_removes_the_metadata_first(
    fs_store: Any, root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    put(fs_store, CLIP)
    removed: list[Path] = []
    for name in ("unlink", "remove"):
        real = getattr(os, name)

        def spy(path: Any, *args: Any, _real: Any = real, **kwargs: Any) -> None:
            removed.append(Path(os.fsdecode(path)))
            _real(path, *args, **kwargs)

        monkeypatch.setattr(os, name, spy)
    fs_store.delete(CLIP)
    assert [p for p in removed if p in (meta_path(root, CLIP), object_path(root, CLIP))] == [
        meta_path(root, CLIP),
        object_path(root, CLIP),
    ]


# @spec STORE-FS-006
def test_a_crash_during_delete_leaves_the_key_uncommitted(fs_store: Any, root: Path) -> None:
    put(fs_store, CLIP)
    final = object_path(root, CLIP)
    with pytest.MonkeyPatch.context() as mp, pytest.raises(Crash):
        for name in ("unlink", "remove"):
            real = getattr(os, name)

            def crash_on_object(path: Any, *args: Any, _real: Any = real, **kwargs: Any) -> None:
                if Path(os.fsdecode(path)) == final:
                    raise Crash("crashed unlinking the object")
                _real(path, *args, **kwargs)

            mp.setattr(os, name, crash_on_object)
        fs_store.delete(CLIP)
    assert fs_store.head(CLIP) is None
    assert list(fs_store.list("clips/")) == []


# --- Modes ----------------------------------------------------------------------------------


# @spec STORE-FS-007
def test_directories_and_files_get_their_modes(fs_store: Any, root: Path) -> None:
    old = os.umask(0o022)
    try:
        put(fs_store, ARCHIVE, metadata={"mask-version": "1"})
        put(fs_store, RUN)
    finally:
        os.umask(old)
    archive_file = object_path(root, ARCHIVE)
    created = [archive_file.parent, meta_path(root, ARCHIVE).parent]
    created += [p for p in archive_file.parents if p != root and p.is_relative_to(root)]
    for directory in created:
        assert mode(directory) == 0o750, directory
    assert mode(archive_file) == 0o440
    assert mode(object_path(root, RUN)) == 0o640


# --- Overwrite race -------------------------------------------------------------------------


# @spec STORE-FS-010, STORE-API-009
def test_a_change_after_the_head_fails_the_overwrite(fs_store: Any, root: Path) -> None:
    put(fs_store, ARCHIVE, metadata={"mask-version": "1"})
    elsewhere = open_store(config.load(with_sites=False))
    changed = b"replaced by another writer\n"
    real = os.fsync
    state = {"done": False, "busy": False}

    def change_once(fd: int) -> None:
        real(fd)
        if not state["done"] and not state["busy"]:
            state["busy"] = True
            put(elsewhere, ARCHIVE, changed, metadata={"mask-version": "3"}, overwrite=True)
            state.update(done=True, busy=False)

    with pytest.MonkeyPatch.context() as mp, pytest.raises(StorageError) as caught:
        mp.setattr(os, "fsync", change_once)
        put(fs_store, ARCHIVE, OTHER, metadata={"mask-version": "2"}, overwrite=True)
    assert caught.value.reason_code == "store_object_changed"
    assert state["done"]
    assert fs_store.head(ARCHIVE).metadata == {"mask-version": "3"}
    assert object_path(root, ARCHIVE).read_bytes() == changed


# --- DuckDB and same-filesystem -------------------------------------------------------------


# @spec STORE-FS-009
def test_table_glob_and_no_duckdb_setup(fs_store: Any, root: Path) -> None:
    assert fs_store.table_glob("curated/detections/") == f"{root}/curated/detections/**/*.parquet"

    class Untouchable:
        def __getattr__(self, name: str) -> Any:
            raise AssertionError(f"configure_duckdb used conn.{name}")

    fs_store.configure_duckdb(Untouchable())


# @spec STORE-SAME-001
def test_same_filesystem_compares_devices(fs_store: Any, fs_env: Project, root: Path) -> None:
    assert fs_store.same_filesystem_as(fs_env.home / "urbanbio-dev" / "quarantine") is True
    other = next(
        (
            Path(p)
            for p in ("/dev/shm", "/run", "/proc")
            if Path(p).is_dir() and Path(p).stat().st_dev != root.stat().st_dev
        ),
        None,
    )
    if other is None:
        pytest.skip("no directory on another filesystem")
    assert fs_store.same_filesystem_as(other) is False
