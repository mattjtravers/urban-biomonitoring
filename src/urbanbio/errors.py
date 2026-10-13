"""Exception hierarchy and reason codes (config-cli LLD § Errors)."""

from __future__ import annotations


# @spec CLI-ERR-001
class UrbanbioError(Exception):
    """Base of every error the pipeline raises on purpose.

    `reason_code` is a short constant (such as `checksum_mismatch`) recorded in the ledger and
    the run record. It is required, so every flagged unit records a specific reason.
    """

    def __init__(self, message: str, *, reason_code: str) -> None:
        super().__init__(message)
        self.reason_code = reason_code


class ConfigError(UrbanbioError):
    """Bad or missing configuration, missing private values, or a store marker mismatch."""


class PrivacyError(UrbanbioError):
    """A privacy guard tripped. Stage code never catches it."""


class IntegrityError(UrbanbioError):
    """A checksum or bit-identity mismatch."""


class AdapterError(UrbanbioError):
    """Unparseable or inconsistent device data."""


class TimeNormalizationError(UrbanbioError):
    """Missing or contradictory time-zone information."""


class StorageError(UrbanbioError):
    """A store failure after the backend's retries."""


class LockError(UrbanbioError):
    """Another process holds the pipeline lock."""
