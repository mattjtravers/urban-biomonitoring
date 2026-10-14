"""Every store key (store LLD § Key Layout). No other module formats a key."""

from __future__ import annotations

import hashlib
import re
from collections import defaultdict
from collections.abc import Iterable, Mapping
from datetime import UTC, datetime

from urbanbio.storage.store import MARKER_KEY

MARKER = MARKER_KEY

_UNSAFE = re.compile(r"[^A-Za-z0-9._=-]")


def _archive_prefix(site_id: str, deployment_id: str, start_utc: datetime) -> str:
    if start_utc.tzinfo is None:
        raise ValueError("start_utc must be timezone-aware")
    date = start_utc.astimezone(UTC).date().isoformat()
    return f"archive/site={site_id}/deployment={deployment_id}/date={date}/"


# @spec STORE-KEY-001, STORE-KEY-002
def archive_audio(site_id: str, deployment_id: str, start_utc: datetime, audio_file_id: str) -> str:
    return _archive_prefix(site_id, deployment_id, start_utc) + f"{audio_file_id}.flac"


# @spec STORE-KEY-002
def archive_header(
    site_id: str, deployment_id: str, start_utc: datetime, audio_file_id: str
) -> str:
    return _archive_prefix(site_id, deployment_id, start_utc) + f"{audio_file_id}.header.json"


def _retrieval_prefix(site_id: str, deployment_id: str, retrieval_id: str) -> str:
    return f"retrievals/site={site_id}/deployment={deployment_id}/{retrieval_id}/"


# @spec STORE-KEY-003
def retrieval_manifest(site_id: str, deployment_id: str, retrieval_id: str, part: int) -> str:
    return _retrieval_prefix(site_id, deployment_id, retrieval_id) + f"manifest-part{part:02d}.json"


def _sanitize(device_path: str) -> list[str]:
    segments = [seg for seg in device_path.split("/") if seg]
    out = []
    for seg in segments:
        seg = _UNSAFE.sub("_", seg)
        out.append("_" + seg[1:] if seg.startswith(".") else seg)
    return out


def _with_path_hash(segments: list[str], original: str) -> list[str]:
    suffix = "-" + hashlib.sha256(original.encode()).hexdigest()[:8]
    name = segments[-1]
    stem, dot, ext = name.rpartition(".")
    renamed = f"{stem}{suffix}.{ext}" if dot and stem else name + suffix
    return [*segments[:-1], renamed]


# @spec STORE-KEY-004, STORE-KEY-005
def device_file_keys(
    site_id: str,
    deployment_id: str,
    retrieval_id: str,
    part: int,
    device_paths: Iterable[str],
) -> dict[str, str]:
    """Keys for one ingest part's device files, by original path relative to the card root.

    Paths that sanitize to the same key each get the first 8 hex characters of the SHA-256 of
    their original path before the extension. The caller records the mapping in the manifest.
    """
    prefix = _retrieval_prefix(site_id, deployment_id, retrieval_id) + f"device/part{part:02d}/"
    sanitized = {original: _sanitize(original) for original in device_paths}
    groups: dict[str, list[str]] = defaultdict(list)
    for original, segments in sanitized.items():
        groups["/".join(segments)].append(original)
    keys = {}
    for original, segments in sanitized.items():
        if len(groups["/".join(segments)]) > 1:
            segments = _with_path_hash(segments, original)
        keys[original] = prefix + "/".join(segments)
    return keys


# @spec STORE-KEY-006
def curated_file(table: str, partitions: Mapping[str, str], name: str) -> str:
    parts = "".join(f"{column}={value}/" for column, value in partitions.items())
    return f"curated/{table}/{parts}{name}.parquet"


# @spec STORE-KEY-006
def clip(detection_id: str) -> str:
    return f"clips/{detection_id}.flac"


# @spec STORE-KEY-006
def run_record(run_id: str) -> str:
    return f"runs/{run_id}.json"


# @spec STORE-KEY-006
def run_log(run_id: str) -> str:
    return f"runs/{run_id}.log"
