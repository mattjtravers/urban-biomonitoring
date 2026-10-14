"""Identifiers and canonical JSON (data-model LLD § Identifiers; runs LLD § Units and
Idempotency Keys)."""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime, timedelta, timezone

import pytest
from pydantic import BaseModel, ConfigDict, ValidationError

from urbanbio.models import ids

# --- Canonical JSON -------------------------------------------------------------------------


# @spec RUN-KEY-003
def test_canonical_json_sorts_keys_without_whitespace() -> None:
    obj = {"b": [1, 2], "a": {"d": "x", "c": None}}
    assert ids.canonical_json(obj) == '{"a":{"c":null,"d":"x"},"b":[1,2]}'
    assert ids.canonical_json(obj) == json.dumps(obj, sort_keys=True, separators=(",", ":"))


# @spec RUN-KEY-003
@pytest.mark.parametrize(
    ("value", "text"), [(0.1, "0.1"), (1.0, "1.0"), (1e-7, "1e-07"), (2.5, "2.5")]
)
def test_canonical_json_writes_floats_in_repr_form(value: float, text: str) -> None:
    assert ids.canonical_json({"x": value}) == '{"x":' + text + "}"


# @spec RUN-KEY-003
def test_canonical_sha256_hashes_the_canonical_text() -> None:
    obj = {"z": 1, "a": 2.5}
    expected = hashlib.sha256(b'{"a":2.5,"z":1}').hexdigest()
    assert ids.canonical_sha256(obj) == expected


# @spec DATA-ID-004
@pytest.mark.parametrize(
    ("value", "text"),
    [
        (datetime(2026, 3, 1, 12, 0, tzinfo=UTC), "2026-03-01T12:00:00.000000Z"),
        (datetime(2026, 3, 1, 12, 0, 0, 5, tzinfo=UTC), "2026-03-01T12:00:00.000005Z"),
        (
            datetime(2026, 3, 1, 7, 0, tzinfo=timezone(timedelta(hours=-5))),
            "2026-03-01T12:00:00.000000Z",
        ),
    ],
)
def test_utc_string_has_one_form_per_instant(value: datetime, text: str) -> None:
    assert ids.utc_string(value) == text


# @spec DATA-ID-004
def test_utc_string_refuses_a_naive_datetime() -> None:
    with pytest.raises(ValueError):
        ids.utc_string(datetime(2026, 3, 1, 12, 0))


# --- Derived IDs ----------------------------------------------------------------------------


def h16(obj: dict) -> str:
    text = json.dumps(obj, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(text.encode()).hexdigest()[:16]


# @spec DATA-ID-003
def test_audio_file_id_is_a_prefix_of_the_original_sha256() -> None:
    sha = hashlib.sha256(b"synthetic original").hexdigest()
    assert ids.audio_file_id(sha) == "af-" + sha[:16]


# @spec DATA-ID-003
def test_audio_file_id_refuses_a_non_sha256() -> None:
    with pytest.raises(ValueError):
        ids.audio_file_id("not-a-checksum")


# @spec DATA-ID-004
def test_hashed_ids() -> None:
    af = "af-0123456789abcdef"
    assert ids.speech_event_id(af, "screen-20261012T140500Z-ab12", "silero-vad", 12.5) == (
        "se-"
        + h16(
            {
                "audio_file_id": af,
                "run_id": "screen-20261012T140500Z-ab12",
                "detector": "silero-vad",
                "start_s": 12.5,
            }
        )
    )
    det = ids.detection_id(
        "birdnet-acoustic-2.4-fp32", af, 3.0, "Turdus migratorius_American Robin"
    )
    assert det == "det-" + h16(
        {
            "model_id": "birdnet-acoustic-2.4-fp32",
            "audio_file_id": af,
            "start_s": 3.0,
            "label": "Turdus migratorius_American Robin",
        }
    )
    labeled = datetime(2026, 3, 1, 12, 0, tzinfo=UTC)
    assert ids.label_id(det, "reviewer1", labeled) == "lbl-" + h16(
        {
            "detection_id": det,
            "reviewer_id": "reviewer1",
            "labeled_utc": "2026-03-01T12:00:00.000000Z",
        }
    )
    assert ids.calibration_id("Turdus migratorius", "birdnet-acoustic-2.4-fp32", "a" * 64) == (
        "cal-"
        + h16(
            {
                "taxon_key": "Turdus migratorius",
                "model_id": "birdnet-acoustic-2.4-fp32",
                "label_set_hash": "a" * 64,
            }
        )
    )


# @spec DATA-ID-005
def test_detection_id_ignores_run_and_mask_version() -> None:
    import inspect

    params = set(inspect.signature(ids.detection_id).parameters)
    assert params == {"model_id", "audio_file_id", "start_s", "label"}
    one = ids.detection_id("m", "af-0123456789abcdef", 3.0, "x")
    assert one == ids.detection_id("m", "af-0123456789abcdef", 3.0, "x")
    assert one != ids.detection_id("m", "af-0123456789abcdef", 6.0, "x")


# --- Formats --------------------------------------------------------------------------------


class Ids(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    entity: ids.EntityId
    site: ids.SiteId
    deployment: ids.DeploymentId
    retrieval: ids.RetrievalId
    reviewer: ids.ReviewerId


GOOD = {
    "entity": "af-0123456789abcdef",
    "site": "site12",
    "deployment": "site12-dep003",
    "retrieval": "site12-dep003-r010",
    "reviewer": "reviewer1",
}


# @spec DATA-ID-001, DATA-ID-006
def test_well_formed_ids_validate() -> None:
    assert Ids(**GOOD).deployment == "site12-dep003"


# @spec DATA-ID-001, DATA-ID-006
@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("entity", "AF-0123"),
        ("entity", "af_0123"),
        ("entity", "af:0123"),
        ("site", "Site1"),
        ("site", "site"),
        ("site", "s1"),
        ("deployment", "site1-dep1"),
        ("deployment", "site1-deployment001"),
        ("retrieval", "site1-dep001-r1"),
        ("retrieval", "site1-r001"),
        ("reviewer", "Jane Doe"),
        ("reviewer", "jane.doe"),
    ],
)
def test_malformed_ids_are_refused(field: str, value: str) -> None:
    with pytest.raises(ValidationError):
        Ids(**{**GOOD, field: value})


# @spec DATA-ID-002
def test_next_deployment_and_retrieval_ids_follow_the_highest() -> None:
    assert ids.next_deployment_id("site1", []) == "site1-dep001"
    assert ids.next_deployment_id("site1", ["site1-dep001", "site1-dep007", "site2-dep009"]) == (
        "site1-dep008"
    )
    assert ids.next_retrieval_id("site1-dep002", ["site1-dep002-r001", "site1-dep002-r003"]) == (
        "site1-dep002-r004"
    )
    assert ids.next_retrieval_id("site1-dep002", ["site1-dep001-r009"]) == "site1-dep002-r001"
