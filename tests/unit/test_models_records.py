"""Persisted models and their Arrow schemas (data-model LLD § Entities, § Parquet Tables; runs
LLD § Stage-Unit Ledger, § Run Record)."""

from __future__ import annotations

import types
import typing
from datetime import UTC, datetime, time, timedelta, timezone
from typing import Any

import pyarrow as pa
import pytest
from pydantic import BaseModel, ValidationError

from urbanbio.models.deployment import Deployment, RecordingSchedule
from urbanbio.models.run import RunSummary, StageUnit, Waiver
from urbanbio.models.site import Site
from urbanbio.provenance.runs import PRIVATE_KEYS
from urbanbio.storage import tables

NOW = datetime(2026, 3, 1, 12, 0, tzinfo=UTC)


def schedule(**overrides: Any) -> RecordingSchedule:
    values = {
        "duty_on_s": 60,
        "duty_off_s": 240,
        "max_file_length_s": 60,
        "daily_windows": None,
        "utc_offset_minutes": -300,
    }
    return RecordingSchedule(**{**values, **overrides})


def deployment(**overrides: Any) -> Deployment:
    values = {
        "deployment_id": "site1-dep001",
        "site_id": "site1",
        "start_utc": NOW,
        "end_utc": None,
        "device_make": "Wildlife Acoustics",
        "device_model": "Song Meter Micro 2",
        "device_serial": "SMM00001",
        "recorder_name": "rec-a",
        "firmware_version": None,
        "adapter": "songmeter-micro2",
        "sample_rate_hz": 48000,
        "schedule": schedule(),
        "gain_db": None,
        "mount_height_m": 2.0,
        "mount_heading_deg": 180,
        "timestamp_issues": False,
        "tags": [],
        "comments": None,
    }
    return Deployment(**{**values, **overrides})


def stage_unit(**overrides: Any) -> StageUnit:
    values = {
        "stage": "screen",
        "unit_id": "af-0123456789abcdef",
        "idempotency_key": "a" * 64,
        "run_id": "screen-20260301T120000Z-ab12",
        "status": "succeeded",
        "reason_code": None,
        "waived_reason_codes": [],
        "related_unit_id": None,
        "output_refs": [],
        "git_dirty": False,
        "finished_utc": NOW,
    }
    return StageUnit(**{**values, **overrides})


# --- Fields ---------------------------------------------------------------------------------


# @spec DATA-MODEL-001, DATA-DEP-003
def test_recording_schedule_fields() -> None:
    assert set(RecordingSchedule.model_fields) == {
        "duty_on_s",
        "duty_off_s",
        "max_file_length_s",
        "daily_windows",
        "utc_offset_minutes",
    }
    windowed = schedule(daily_windows=[(time(5, 0), time(9, 0))], duty_off_s=0)
    assert windowed.daily_windows == [(time(5, 0), time(9, 0))]


# @spec DATA-MODEL-001
def test_deployment_fields() -> None:
    assert set(Deployment.model_fields) == {
        "deployment_id",
        "site_id",
        "start_utc",
        "end_utc",
        "device_make",
        "device_model",
        "device_serial",
        "recorder_name",
        "firmware_version",
        "adapter",
        "sample_rate_hz",
        "schedule",
        "gain_db",
        "mount_height_m",
        "mount_heading_deg",
        "timestamp_issues",
        "tags",
        "comments",
    }
    assert deployment().timestamp_issues is False


# @spec RUN-LEDGER-001
def test_stage_unit_fields() -> None:
    assert set(StageUnit.model_fields) == {
        "stage",
        "unit_id",
        "idempotency_key",
        "run_id",
        "status",
        "reason_code",
        "waived_reason_codes",
        "related_unit_id",
        "output_refs",
        "git_dirty",
        "finished_utc",
    }


# @spec RUN-WAIVE-001
def test_waiver_fields() -> None:
    assert set(Waiver.model_fields) == {
        "waived_stage",
        "waived_unit_id",
        "reason_code",
        "note",
        "run_id",
        "created_utc",
    }


# @spec RUN-REC-002
def test_run_summary_fields() -> None:
    assert set(RunSummary.model_fields) == {
        "run_id",
        "stage",
        "status",
        "started_utc",
        "ended_utc",
        "package_version",
        "git_commit",
        "git_dirty",
        "stage_version",
        "store_id",
        "params_hash",
        "units_total",
        "skipped",
        "succeeded",
        "flagged",
        "failed",
        "log_key",
    }


# @spec DATA-MODEL-001
@pytest.mark.parametrize(
    "model", [Deployment, RecordingSchedule, StageUnit, Waiver, RunSummary, Site]
)
def test_models_are_frozen_and_forbid_extra_fields(model: type[BaseModel]) -> None:
    assert model.model_config.get("frozen") is True
    assert model.model_config.get("extra") == "forbid"


# @spec DATA-MODEL-001
def test_unknown_fields_are_refused() -> None:
    with pytest.raises(ValidationError):
        deployment(latitude=12.35)


# --- Datetimes ------------------------------------------------------------------------------


# @spec DATA-MODEL-002
@pytest.mark.parametrize(
    "value",
    [datetime(2026, 3, 1, 12, 0), datetime(2026, 3, 1, 12, 0, tzinfo=timezone(timedelta(hours=2)))],
    ids=["naive", "not-utc"],
)
def test_utc_fields_must_be_aware_utc(value: datetime) -> None:
    with pytest.raises(ValidationError):
        deployment(start_utc=value)
    with pytest.raises(ValidationError):
        stage_unit(finished_utc=value)


# @spec DATA-MODEL-002
def test_utc_fields_accept_utc() -> None:
    assert deployment(end_utc=NOW + timedelta(days=7)).end_utc == NOW + timedelta(days=7)


# @spec DATA-MODEL-002
def test_local_fields_must_be_naive() -> None:
    from urbanbio.models.ids import Record

    class Example(Record):
        start_local: datetime

    assert Example(start_local=datetime(2026, 3, 1, 7, 0)).start_local.tzinfo is None
    with pytest.raises(ValidationError):
        Example(start_local=NOW)


# --- Ledger rows ----------------------------------------------------------------------------


# @spec RUN-LEDGER-002
@pytest.mark.parametrize("status", ["flagged", "failed"])
def test_flagged_and_failed_rows_need_a_reason_code(status: str) -> None:
    with pytest.raises(ValidationError):
        stage_unit(status=status, reason_code=None)
    assert stage_unit(status=status, reason_code="truncated").reason_code == "truncated"


# @spec RUN-LEDGER-002
def test_succeeded_rows_carry_no_reason_code_except_a_discard_purge() -> None:
    with pytest.raises(ValidationError):
        stage_unit(reason_code="truncated")
    with pytest.raises(ValidationError):
        stage_unit(stage="archive", reason_code="discarded")
    row = stage_unit(stage="purge", reason_code="discarded")
    assert row.reason_code == "discarded"


# --- Privacy --------------------------------------------------------------------------------


# @spec DATA-MODEL-003
def test_no_persisted_model_or_schema_has_a_private_field() -> None:
    for name, spec in tables.TABLES.items():
        columns = set(spec.schema.names) | set(spec.model.model_fields)
        assert not columns & set(PRIVATE_KEYS), name
        assert not any("exact" in c or "address" in c for c in columns), name


# --- Arrow schemas --------------------------------------------------------------------------


def _arrow_type(annotation: Any) -> pa.DataType | None:
    """The Arrow type a scalar annotation maps to, or None for a nested type."""
    scalar = {
        str: pa.string(),
        int: pa.int64(),
        float: pa.float64(),
        bool: pa.bool_(),
        datetime: pa.timestamp("us", tz="UTC"),
    }
    if annotation in scalar:
        return scalar[annotation]
    if typing.get_origin(annotation) is typing.Literal:
        return pa.string()
    if typing.get_origin(annotation) is list and typing.get_args(annotation) == (str,):
        return pa.list_(pa.string())
    return None


def _unwrap(annotation: Any) -> tuple[Any, bool]:
    if isinstance(annotation, types.UnionType) or typing.get_origin(annotation) is typing.Union:
        args = [a for a in typing.get_args(annotation) if a is not type(None)]
        return args[0], True
    return annotation, False


# @spec DATA-PQ-001
@pytest.mark.parametrize("name", sorted(tables.TABLES))
def test_arrow_schema_agrees_with_the_model(name: str) -> None:
    spec = tables.TABLES[name]
    added = {"stage", "unit_id", "run_id"} if spec.unit_scoped else set()
    expected = set(spec.model.model_fields) | added
    assert set(spec.schema.names) == expected
    for field_name, info in spec.model.model_fields.items():
        annotation, optional = _unwrap(info.annotation)
        field = spec.schema.field(field_name)
        assert field.nullable == optional, field_name
        arrow_type = _arrow_type(annotation)
        if arrow_type is not None:
            assert field.type == arrow_type, field_name


# @spec DATA-PQ-001, DATA-PQ-006
def test_tables_in_this_batch_are_registered() -> None:
    assert {"stage_units", "deployments", "sites", "waivers", "runs"} <= set(tables.TABLES)
    assert tables.TABLES["waivers"].unit_scoped
    assert tables.TABLES["deployments"].unit_scoped
    for name in ("stage_units", "sites", "runs"):
        assert not tables.TABLES[name].unit_scoped, name
