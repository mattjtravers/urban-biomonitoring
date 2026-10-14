"""Unit IDs, idempotency keys, and ledger lookups (runs LLD § Units and Idempotency Keys,
§ Lookups, § Waivers)."""

from __future__ import annotations

import hashlib
import json
from datetime import timedelta
from typing import Any

import pytest

from tests.unit.test_models_records import stage_unit
from tests.unit.test_tables import T0, WAIVER_UNIT, write_waiver
from urbanbio.provenance import ledger
from urbanbio.storage import tables

AF = "af-0123456789abcdef"
KEY = "k" * 64


@pytest.fixture
def store(fs_store: Any) -> Any:
    return fs_store


# --- Unit IDs -------------------------------------------------------------------------------


# @spec RUN-KEY-001
def test_unit_ids() -> None:
    assert ledger.file_unit_id(AF) == AF
    assert ledger.mask_unit_id(AF, 1) == f"{AF}:v1"
    assert (
        ledger.detect_unit_id(AF, "birdnet-acoustic-2.4-fp32") == f"{AF}:birdnet-acoustic-2.4-fp32"
    )
    assert ledger.admin_unit_id("site1-dep001") == "site1-dep001"
    assert ledger.waiver_unit_id("archive", f"{AF}:v1", "archive_conflict") == (
        f"archive:{AF}:v1:archive_conflict"
    )


# @spec RUN-KEY-001
def test_unit_ids_split_back_into_their_parts() -> None:
    assert ledger.split_unit_id(f"{AF}:v2") == [AF, "v2"]
    assert ledger.split_unit_id(AF) == [AF]


# @spec RUN-KEY-001
@pytest.mark.parametrize("bad", ["AF-1", "af:1", "", "af 1"])
def test_unit_id_parts_must_be_entity_ids(bad: str) -> None:
    with pytest.raises(ValueError):
        ledger.mask_unit_id(bad, 1)


# --- Keys -----------------------------------------------------------------------------------


def canonical(obj: Any) -> str:
    return json.dumps(obj, sort_keys=True, separators=(",", ":"))


# @spec RUN-KEY-006
def test_params_hash_is_the_sha256_of_canonical_params() -> None:
    params = {"padding_s": 2.0, "labels": ["Human vocal_Human vocal"], "threshold": 0.1}
    assert ledger.params_hash(params) == hashlib.sha256(canonical(params).encode()).hexdigest()
    assert ledger.params_hash(params) == ledger.params_hash(dict(reversed(params.items())))


# @spec RUN-KEY-002
def test_idempotency_key_hashes_the_canonical_document() -> None:
    doc = {
        "stage": "detect",
        "stage_version": 1,
        "unit_id": f"{AF}:birdnet-acoustic-2.4-fp32",
        "input_checksums": ["b" * 64],
        "params_hash": "c" * 64,
        "models": ["birdnet-acoustic-2.4-fp32:" + "d" * 64],
    }
    assert ledger.idempotency_key(**doc) == hashlib.sha256(canonical(doc).encode()).hexdigest()


# @spec RUN-KEY-002
@pytest.mark.parametrize(
    "change",
    [
        {"stage_version": 2},
        {"input_checksums": ["e" * 64]},
        {"params_hash": "f" * 64},
        {"models": []},
        {"unit_id": f"{AF}:v2"},
    ],
)
def test_every_part_of_the_key_changes_it(change: dict[str, Any]) -> None:
    base = {
        "stage": "archive",
        "stage_version": 1,
        "unit_id": f"{AF}:v1",
        "input_checksums": ["b" * 64],
        "params_hash": "c" * 64,
        "models": ["m:" + "d" * 64],
    }
    assert ledger.idempotency_key(**base) != ledger.idempotency_key(**{**base, **change})


# @spec RUN-KEY-007
def test_the_key_has_no_git_input() -> None:
    import inspect

    assert set(inspect.signature(ledger.idempotency_key).parameters) == {
        "stage",
        "stage_version",
        "unit_id",
        "input_checksums",
        "params_hash",
        "models",
    }


# --- Lookups --------------------------------------------------------------------------------


def write(store: Any, *units: Any) -> None:
    for i, row in enumerate(units, start=1):
        tables.write_part(store, "stage_units", [row], name=f"{row.run_id}-{i:04d}")


def unit(
    run: int,
    status: str = "succeeded",
    *,
    dirty: bool = False,
    hours: int = 0,
    key: str = KEY,
    reason: str | None = None,
) -> Any:
    if status != "succeeded" and reason is None:
        reason = "truncated"
    return stage_unit(
        stage="ingest",
        unit_id=AF,
        idempotency_key=key,
        run_id=f"ingest-20260301T120000Z-{run:04d}",
        status=status,
        reason_code=reason,
        git_dirty=dirty,
        finished_utc=T0 + timedelta(hours=hours),
    )


def decide(store: Any, *, dirty: bool = False, key: str = KEY) -> str:
    return ledger.Ledger(tables.connect(store)).decide("ingest", AF, key, git_dirty=dirty)


# @spec RUN-LOOKUP-001
def test_no_row_means_run(store: Any) -> None:
    assert decide(store) == "run"


# @spec RUN-LOOKUP-001
def test_a_succeeded_row_means_skip(store: Any) -> None:
    write(store, unit(1))
    assert decide(store) == "skip"


# @spec RUN-LOOKUP-001
def test_a_failed_row_means_run(store: Any) -> None:
    write(store, unit(1), unit(2, "failed", hours=1))
    assert decide(store) == "run"


# @spec RUN-LOOKUP-001
def test_the_latest_row_decides(store: Any) -> None:
    write(store, unit(1, "failed"), unit(2, hours=1))
    assert decide(store) == "skip"


# @spec RUN-LOOKUP-001
def test_a_row_under_another_key_doesnt_count(store: Any) -> None:
    write(store, unit(1))
    assert decide(store, key="z" * 64) == "run"


# @spec RUN-LOOKUP-001
def test_a_flagged_row_means_skip(store: Any) -> None:
    write(store, unit(1, "flagged"))
    assert decide(store) == "skip"


# @spec RUN-LOOKUP-001, RUN-WAIVE-004
def test_a_waiver_for_the_flag_means_run_whatever_its_key(store: Any) -> None:
    write(store, unit(1, "flagged"))
    write_waiver(store, "admin-20260302T120000Z-0001")
    tables.write_part(
        store,
        "stage_units",
        [
            stage_unit(
                stage="admin",
                unit_id=WAIVER_UNIT,
                idempotency_key="w" * 64,
                run_id="admin-20260302T120000Z-0001",
                finished_utc=T0 + timedelta(days=1),
            )
        ],
        name="admin-20260302T120000Z-0001-0001",
    )
    assert decide(store) == "run"


# @spec RUN-WAIVE-004
def test_a_waiver_for_another_reason_doesnt_apply(store: Any) -> None:
    write(store, unit(1, "flagged", reason="outside_deployment"))
    write_waiver(store, "admin-20260302T120000Z-0001")  # waives `truncated`
    tables.write_part(
        store,
        "stage_units",
        [
            stage_unit(
                stage="admin",
                unit_id=WAIVER_UNIT,
                run_id="admin-20260302T120000Z-0001",
                finished_utc=T0 + timedelta(days=1),
            )
        ],
        name="admin-20260302T120000Z-0001-0001",
    )
    assert decide(store) == "skip"


# @spec RUN-WAIVE-004
def test_an_uncounted_waiver_doesnt_apply(store: Any) -> None:
    write(store, unit(1, "flagged"))
    write_waiver(store, "admin-20260302T120000Z-0001")  # no ledger row: an orphan
    assert decide(store) == "skip"


# @spec RUN-LOOKUP-002
def test_a_clean_run_redoes_a_dirty_success(store: Any) -> None:
    write(store, unit(1, dirty=True))
    assert decide(store, dirty=False) == "run"
    assert decide(store, dirty=True) == "skip"
