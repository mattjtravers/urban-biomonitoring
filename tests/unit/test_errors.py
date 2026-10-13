"""The exception hierarchy and reason codes."""

from __future__ import annotations

import pytest

from urbanbio import errors

SUBCLASSES = (
    "ConfigError",
    "PrivacyError",
    "IntegrityError",
    "AdapterError",
    "TimeNormalizationError",
    "StorageError",
    "LockError",
)


# @spec CLI-ERR-001
@pytest.mark.parametrize("name", SUBCLASSES)
def test_error_derives_from_urbanbio_error_and_carries_reason_code(name: str) -> None:
    cls = getattr(errors, name)
    assert issubclass(cls, errors.UrbanbioError)
    assert issubclass(errors.UrbanbioError, Exception)
    err = cls("checksum differs from manifest", reason_code="checksum_mismatch")
    assert err.reason_code == "checksum_mismatch"
    assert "checksum differs from manifest" in str(err)


# @spec CLI-ERR-001
def test_errors_are_siblings() -> None:
    classes = [getattr(errors, name) for name in SUBCLASSES]
    for cls in classes:
        assert cls.__bases__ == (errors.UrbanbioError,)


# @spec CLI-ERR-001
def test_reason_code_is_required() -> None:
    with pytest.raises(TypeError):
        errors.IntegrityError("no reason code")  # type: ignore[call-arg]
