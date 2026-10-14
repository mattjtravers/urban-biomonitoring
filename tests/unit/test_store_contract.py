"""The `Store` contract, run on every backend (store LLD § Store Interface).

Each test gets an initialized, opened store from the `store` fixture: `filesystem` against a
temporary directory and `s3` against moto. A new backend must pass this file unchanged.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from tests.conftest import sha256_hex
from urbanbio.errors import IntegrityError, StorageError
from urbanbio.storage.store import StoredObject

DATA = b"synthetic object body\n" * 64
OTHER = b"a different synthetic body\n" * 32
RUN = "runs/detect-20261012T140500Z-ab12.json"
ARCHIVE = "archive/site=site1/deployment=site1-dep001/date=2026-03-02/af-0123456789abcdef.flac"
CLIP = "clips/det-0123456789abcdef.flac"


def put(store, key: str, data: bytes = DATA, **kwargs) -> StoredObject:
    return store.put(key, data, sha256=sha256_hex(data), **kwargs)


def read(store, key: str, tmp_path: Path) -> bytes:
    dest = tmp_path / "out" / key.replace("/", "_")
    dest.parent.mkdir(parents=True, exist_ok=True)
    store.get(key, dest)
    return dest.read_bytes()


# --- put ------------------------------------------------------------------------------------


# @spec STORE-API-002, STORE-API-011
def test_put_bytes_returns_the_confirmed_object(store) -> None:
    obj = put(store, RUN, metadata={"run-id": "detect-20261012T140500Z-ab12"})
    expected = StoredObject(
        key=RUN,
        size=len(DATA),
        sha256=sha256_hex(DATA),
        metadata={"run-id": "detect-20261012T140500Z-ab12"},
    )
    assert obj == expected
    assert store.head(RUN) == expected


# @spec STORE-API-002, STORE-API-012
def test_put_from_a_path_round_trips(store, tmp_path: Path) -> None:
    source = tmp_path / "source.bin"
    source.write_bytes(OTHER)
    obj = store.put(CLIP, source, sha256=sha256_hex(OTHER))
    assert obj.size == len(OTHER)
    assert read(store, CLIP, tmp_path) == OTHER


# @spec STORE-API-002, STORE-API-003
def test_put_with_a_wrong_sha256_raises_and_commits_nothing(store) -> None:
    with pytest.raises(IntegrityError) as caught:
        store.put(CLIP, DATA, sha256=sha256_hex(OTHER))
    assert caught.value.reason_code == "store_checksum_mismatch"
    assert store.head(CLIP) is None


# @spec STORE-API-004
def test_put_without_overwrite_refuses_an_existing_object(store, tmp_path: Path) -> None:
    put(store, CLIP)
    with pytest.raises(StorageError) as caught:
        put(store, CLIP, OTHER)
    assert caught.value.reason_code == "store_object_exists"
    assert read(store, CLIP, tmp_path) == DATA


# @spec STORE-API-006
def test_run_records_can_be_overwritten(store, tmp_path: Path) -> None:
    put(store, RUN)
    obj = put(store, RUN, OTHER, overwrite=True)
    assert obj.sha256 == sha256_hex(OTHER)
    assert read(store, RUN, tmp_path) == OTHER


# @spec STORE-API-017
def test_run_record_overwrite_of_a_missing_object_writes_it(store) -> None:
    put(store, RUN, overwrite=True)
    assert store.head(RUN).sha256 == sha256_hex(DATA)


# @spec STORE-API-017
def test_archive_overwrite_of_a_missing_object_writes_it(store) -> None:
    put(store, ARCHIVE, metadata={"mask-version": "1"}, overwrite=True)
    assert store.head(ARCHIVE).metadata == {"mask-version": "1"}


# @spec STORE-API-007
def test_archive_overwrite_with_a_higher_mask_version_replaces(store, tmp_path: Path) -> None:
    put(store, ARCHIVE, metadata={"mask-version": "1"})
    obj = put(store, ARCHIVE, OTHER, metadata={"mask-version": "2"}, overwrite=True)
    assert obj.metadata == {"mask-version": "2"}
    assert read(store, ARCHIVE, tmp_path) == OTHER


# @spec STORE-API-007
def test_mask_versions_compare_as_numbers(store) -> None:
    put(store, ARCHIVE, metadata={"mask-version": "9"})
    obj = put(store, ARCHIVE, OTHER, metadata={"mask-version": "10"}, overwrite=True)
    assert obj.metadata == {"mask-version": "10"}


# @spec STORE-API-008
@pytest.mark.parametrize("new_version", ["2", "1"])
def test_archive_overwrite_without_a_higher_mask_version_is_forbidden(
    store, tmp_path: Path, new_version: str
) -> None:
    put(store, ARCHIVE, metadata={"mask-version": "2"})
    with pytest.raises(StorageError) as caught:
        put(store, ARCHIVE, OTHER, metadata={"mask-version": new_version}, overwrite=True)
    assert caught.value.reason_code == "store_overwrite_forbidden"
    assert read(store, ARCHIVE, tmp_path) == DATA


# @spec STORE-API-008
@pytest.mark.parametrize(
    ("old", "new"),
    [
        ({}, {"mask-version": "2"}),
        ({"mask-version": "1"}, {}),
        ({"mask-version": "1"}, {"mask-version": "2a"}),
        ({"mask-version": "x"}, {"mask-version": "2"}),
        ({"mask-version": "1"}, {"mask-version": "-2"}),
    ],
)
def test_archive_overwrite_needs_integer_mask_versions(
    store, tmp_path: Path, old: dict[str, str], new: dict[str, str]
) -> None:
    put(store, ARCHIVE, metadata=old)
    with pytest.raises(StorageError) as caught:
        put(store, ARCHIVE, OTHER, metadata=new, overwrite=True)
    assert caught.value.reason_code == "store_overwrite_forbidden"
    assert read(store, ARCHIVE, tmp_path) == DATA


# @spec STORE-API-008
@pytest.mark.parametrize(
    "key",
    [
        CLIP,
        "curated/detections/site_id=site1/detect-20261012T140500Z-ab12.parquet",
        "retrievals/site=site1/deployment=site1-dep001/site1-dep001-r001/manifest-part01.json",
        ".urbanbio-store.json",
    ],
)
def test_overwrite_outside_runs_and_archive_is_forbidden_before_writing(store, key: str) -> None:
    before = store.head(key)
    with pytest.raises(StorageError) as caught:
        put(store, key, OTHER, overwrite=True)
    assert caught.value.reason_code == "store_overwrite_forbidden"
    assert store.head(key) == before


# @spec STORE-API-010
@pytest.mark.parametrize(
    "metadata",
    [
        {"Mask-Version": "1"},
        {"mask_version": "1"},
        {"mask version": "1"},
        {"note": "café"},
        {"café": "1"},
        {"note": "x" * 2100},
        {f"k{i:03d}": "x" * 20 for i in range(100)},
    ],
)
def test_invalid_metadata_raises_before_writing(store, metadata: dict[str, str]) -> None:
    with pytest.raises(ValueError):
        put(store, CLIP, metadata=metadata)
    assert store.head(CLIP) is None


# @spec STORE-API-010
def test_metadata_within_the_limits_is_kept(store) -> None:
    metadata = {"mask-version": "1", "note": "x" * 1000}
    assert put(store, CLIP, metadata=metadata).metadata == metadata
    assert store.head(CLIP).metadata == metadata


# --- head, get, list, delete ----------------------------------------------------------------


# @spec STORE-API-011
def test_head_of_a_missing_key_is_none(store) -> None:
    assert store.head(CLIP) is None


# @spec STORE-API-012
def test_get_returns_the_object_it_wrote(store, tmp_path: Path) -> None:
    put(store, ARCHIVE, metadata={"mask-version": "1"})
    dest = tmp_path / "copy.flac"
    obj = store.get(ARCHIVE, dest)
    assert dest.read_bytes() == DATA
    assert obj.sha256 == sha256_hex(DATA)
    assert obj.metadata == {"mask-version": "1"}


# @spec STORE-API-013
def test_list_yields_committed_keys_under_a_prefix_in_order(store) -> None:
    keys = [
        "runs/c.json",
        "runs/a.json",
        "runs/b/z.log",
        "runs/b.log",
        "clips/det-0000000000000001.flac",
    ]
    for key in keys:
        put(store, key)
    assert list(store.list("runs/")) == ["runs/a.json", "runs/b.log", "runs/b/z.log", "runs/c.json"]
    assert list(store.list("clips/")) == ["clips/det-0000000000000001.flac"]
    assert list(store.list("archive/")) == []


# @spec STORE-API-014
def test_delete_removes_a_clip(store) -> None:
    put(store, CLIP)
    store.delete(CLIP)
    assert store.head(CLIP) is None
    assert list(store.list("clips/")) == []


# @spec STORE-API-014
@pytest.mark.parametrize("key", [ARCHIVE, RUN, ".urbanbio-store.json"])
def test_delete_outside_clips_is_forbidden(store, key: str) -> None:
    if store.head(key) is None:
        put(store, key, metadata={"mask-version": "1"} if key == ARCHIVE else None)
    before = store.head(key)
    with pytest.raises(StorageError) as caught:
        store.delete(key)
    assert caught.value.reason_code == "store_delete_forbidden"
    assert store.head(key) == before


# --- keys -----------------------------------------------------------------------------------

BAD_KEYS = [
    "",
    "/runs/a.json",
    "runs//a.json",
    "runs/a.json/",
    "runs/./a.json",
    "runs/../a.json",
    "..",
    "runs/a b.json",
    "runs/café.json",
    "runs\\a.json",
    "runs/a.json?x",
    ".hidden",
    "runs/.meta/a.json",
    "runs/.a.json.tmp-0001",
    "runs/.urbanbio-store.json",
]


# @spec STORE-API-015
@pytest.mark.parametrize("key", BAD_KEYS)
def test_invalid_keys_are_refused_by_every_method(store, key: str, tmp_path: Path) -> None:
    with pytest.raises(ValueError):
        put(store, key)
    with pytest.raises(ValueError):
        store.head(key)
    with pytest.raises(ValueError):
        store.get(key, tmp_path / "out")
    with pytest.raises(ValueError):
        store.delete(key)


# @spec STORE-API-015
def test_the_marker_is_the_only_key_with_a_leading_dot(store) -> None:
    assert store.head(".urbanbio-store.json") is not None


# @spec STORE-API-015
def test_keys_may_hold_dots_equals_and_hyphens(store) -> None:
    key = "curated/x_y/site_id=site1/a.b-c.parquet"
    put(store, key)
    assert store.head(key) is not None
