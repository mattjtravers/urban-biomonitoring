"""Store key layout (store LLD § Key Layout), built only by `urbanbio.storage.paths`."""

from __future__ import annotations

import hashlib
import re
from datetime import UTC, datetime, timedelta, timezone

from urbanbio.storage import paths

KEY_RE = re.compile(r"^(?!/)(?:(?!\.{1,2}/)[A-Za-z0-9._=-]+/)*(?!\.{1,2}$)[A-Za-z0-9._=-]+$")


def h8(original: str) -> str:
    return hashlib.sha256(original.encode()).hexdigest()[:8]


# --- Archive and retrievals ----------------------------------------------------------------


# @spec STORE-KEY-002
def test_archive_keys_use_the_utc_date_of_the_file_start() -> None:
    # 23:30 at UTC-5 is the next day in UTC.
    start = datetime(2026, 3, 1, 23, 30, tzinfo=timezone(timedelta(hours=-5)))
    prefix = "archive/site=site1/deployment=site1-dep001/date=2026-03-02/"
    assert (
        paths.archive_audio("site1", "site1-dep001", start, "af-0123456789abcdef")
        == prefix + "af-0123456789abcdef.flac"
    )
    assert (
        paths.archive_header("site1", "site1-dep001", start, "af-0123456789abcdef")
        == prefix + "af-0123456789abcdef.header.json"
    )


# @spec STORE-KEY-002
def test_archive_key_date_for_a_utc_start() -> None:
    start = datetime(2026, 12, 31, 23, 59, 59, tzinfo=UTC)
    key = paths.archive_audio("site2", "site2-dep003", start, "af-ffffffffffffffff")
    assert (
        key == "archive/site=site2/deployment=site2-dep003/date=2026-12-31/af-ffffffffffffffff.flac"
    )


# @spec STORE-KEY-003
def test_manifest_key_has_a_two_digit_part() -> None:
    assert (
        paths.retrieval_manifest("site1", "site1-dep001", "site1-dep001-r004", 3)
        == "retrievals/site=site1/deployment=site1-dep001/site1-dep001-r004/manifest-part03.json"
    )


# @spec STORE-KEY-004
def test_device_file_keys_sanitize_each_segment() -> None:
    prefix = "retrievals/site=site1/deployment=site1-dep001/site1-dep001-r004/device/part02/"
    keys = paths.device_file_keys(
        "site1",
        "site1-dep001",
        "site1-dep001-r004",
        2,
        ["Data/SMM12345_Summary.txt", "LOGS/Log File (1).txt", ".hidden/.cfg", "Diag/ü.bin"],
    )
    assert keys == {
        "Data/SMM12345_Summary.txt": prefix + "Data/SMM12345_Summary.txt",
        "LOGS/Log File (1).txt": prefix + "LOGS/Log_File__1_.txt",
        ".hidden/.cfg": prefix + "_hidden/_cfg",
        "Diag/ü.bin": prefix + "Diag/_.bin",
    }
    assert all(KEY_RE.match(key) for key in keys.values())


# @spec STORE-KEY-004
def test_device_files_of_different_parts_never_share_a_key() -> None:
    args = ("site1", "site1-dep001", "site1-dep001-r004")
    one = paths.device_file_keys(*args, 1, ["SMM12345_Summary.txt"])
    two = paths.device_file_keys(*args, 2, ["SMM12345_Summary.txt"])
    assert one["SMM12345_Summary.txt"] != two["SMM12345_Summary.txt"]
    assert "/device/part01/" in one["SMM12345_Summary.txt"]
    assert "/device/part02/" in two["SMM12345_Summary.txt"]


# @spec STORE-KEY-005
def test_colliding_device_paths_get_a_path_hash_before_the_extension() -> None:
    prefix = "retrievals/site=site1/deployment=site1-dep001/site1-dep001-r004/device/part01/"
    keys = paths.device_file_keys(
        "site1",
        "site1-dep001",
        "site1-dep001-r004",
        1,
        ["log a.txt", "log_a.txt", "READ ME", "READ_ME", "other.txt"],
    )
    assert keys == {
        "log a.txt": prefix + f"log_a-{h8('log a.txt')}.txt",
        "log_a.txt": prefix + f"log_a-{h8('log_a.txt')}.txt",
        "READ ME": prefix + f"READ_ME-{h8('READ ME')}",
        "READ_ME": prefix + f"READ_ME-{h8('READ_ME')}",
        "other.txt": prefix + "other.txt",
    }


# --- Curated tables, clips, runs, marker ----------------------------------------------------


# @spec STORE-KEY-006
def test_curated_clip_and_run_keys() -> None:
    assert (
        paths.curated_file("detections", {"site_id": "site1", "year": "2026"}, "run-1")
        == "curated/detections/site_id=site1/year=2026/run-1.parquet"
    )
    assert paths.clip("det-0123456789abcdef") == "clips/det-0123456789abcdef.flac"
    assert (
        paths.run_record("detect-20261012T140500Z-ab12") == "runs/detect-20261012T140500Z-ab12.json"
    )
    assert paths.run_log("detect-20261012T140500Z-ab12") == "runs/detect-20261012T140500Z-ab12.log"


# @spec STORE-MARK-001
def test_marker_key() -> None:
    assert paths.MARKER == ".urbanbio-store.json"
