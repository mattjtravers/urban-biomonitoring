"""The store marker, `urbanbio store init`, and the store-ID check (store LLD § Store Marker,
§ Changing Stores)."""

from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import secrets
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any

from urbanbio.errors import ConfigError
from urbanbio.storage.filesystem import FilesystemStore
from urbanbio.storage.store import MARKER_KEY, BaseStore, build_store

if TYPE_CHECKING:
    from urbanbio.config import PipelineConfig

SCHEMA = "urbanbio.store-marker/1"
STORE_ID_FILE = "store-id"
# DATA-ID-001: `<deployment_id>-r<NNN>`, where `deployment_id` is `<site_id>-dep<NNN>`.
RETRIEVAL_ID = re.compile(r"site[0-9]+-dep[0-9]{3,}-r[0-9]{3,}")

log = logging.getLogger("urbanbio.storage")


# @spec STORE-MARK-001, STORE-MARK-002, STORE-MARK-003, STORE-MARK-004
def init_store(cfg: PipelineConfig) -> dict[str, Any]:
    """Write the marker for the current profile to an empty store, and return it.

    Every refusal is a ConfigError and writes nothing.
    """
    store = build_store(cfg)
    if isinstance(store, FilesystemStore):
        if not store.root.is_dir():
            raise ConfigError(
                f"store.root doesn't exist as a directory: {store.root}. Create it (or mount the "
                "share) first; `urbanbio store init` never creates the root",
                reason_code="store_root_missing",
            )
        if cfg.profile == "processing" and store.same_filesystem_as(cfg.paths.quarantine):
            raise ConfigError(
                "store.root is on the same filesystem as paths.quarantine, so the archive "
                "wouldn't survive the loss of this disk (or the share isn't mounted)",
                reason_code="store_same_filesystem",
            )
        if any(store.root.iterdir()):
            raise _refuse_existing(store)
    elif store.read_bytes(MARKER_KEY) is not None or next(store.list(""), None) is not None:
        raise _refuse_existing(store)

    marker = {
        "schema": SCHEMA,
        "store_id": f"st-{secrets.token_hex(8)}",
        "profile": cfg.profile,
        "created_utc": datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
    }
    body = json.dumps(marker, indent=2).encode() + b"\n"
    store.put(MARKER_KEY, body, sha256=hashlib.sha256(body).hexdigest())
    return marker


def _refuse_existing(store: BaseStore) -> ConfigError:
    if store.head(MARKER_KEY) is not None:
        return ConfigError(
            "the store already has a marker; `urbanbio store init` never re-initializes a store",
            reason_code="store_marker_exists",
        )
    return ConfigError(
        "the store isn't empty; `urbanbio store init` only initializes an empty store",
        reason_code="store_not_empty",
    )


# @spec STORE-MARK-005, STORE-MARK-006, STORE-MARK-007
def check_marker(store: BaseStore, cfg: PipelineConfig) -> dict[str, Any]:
    """Read the marker before any other store access and check it against the profile."""
    body = store.read_bytes(MARKER_KEY)
    if body is None:
        message = "the store has no marker; run `urbanbio store init` to initialize a new store"
        if isinstance(store, FilesystemStore):
            message += (
                ". If store.root is a network share, check that it is mounted: an unmounted "
                "share shows as an empty directory"
            )
        raise ConfigError(message, reason_code="store_marker_missing")
    try:
        marker = json.loads(body)
        valid = marker.get("schema") == SCHEMA and re.fullmatch(
            r"st-[0-9a-f]{16}", str(marker.get("store_id"))
        )
    except (ValueError, AttributeError):
        valid = False
    if not valid:
        raise ConfigError(
            "the store marker isn't a valid marker", reason_code="store_marker_invalid"
        )
    if marker.get("profile") != cfg.profile:
        raise ConfigError(
            f"the store was initialized for the {marker.get('profile')} profile, not {cfg.profile}",
            reason_code="store_profile_mismatch",
        )
    _check_store_id(marker["store_id"], cfg)
    return marker


# @spec STORE-SWITCH-001, STORE-SWITCH-002, STORE-SWITCH-003
def _check_store_id(store_id: str, cfg: PipelineConfig) -> None:
    recorded_path = Path(cfg.paths.state) / STORE_ID_FILE
    try:
        recorded = recorded_path.read_text().strip()
    except FileNotFoundError:
        recorded = None
    if recorded == store_id:
        return
    if recorded is not None:
        in_flight = _retrievals_in_quarantine(Path(cfg.paths.quarantine))
        if in_flight:
            raise ConfigError(
                f"this host last used store {recorded}, but the configured store is {store_id}, "
                f"and the quarantine holds retrievals in flight: {', '.join(in_flight)}. Their "
                "ledger rows are in the previous store; switch back to it to finish them",
                reason_code="store_changed",
            )
        log.warning(
            "the store changed from %s to %s; nothing is in flight in the quarantine",
            recorded,
            store_id,
        )
    recorded_path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    tmp = recorded_path.with_name(f".{STORE_ID_FILE}.tmp")
    tmp.write_text(store_id + "\n")
    os.replace(tmp, recorded_path)


def _retrievals_in_quarantine(quarantine: Path) -> list[str]:
    if not quarantine.is_dir():
        return []
    return sorted(
        entry.name
        for entry in quarantine.iterdir()
        if entry.is_dir() and RETRIEVAL_ID.fullmatch(entry.name)
    )
