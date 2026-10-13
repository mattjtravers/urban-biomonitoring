"""The Site entity (data-model LLD § Site)."""

from __future__ import annotations

import functools
import zoneinfo

from pydantic import BaseModel, ConfigDict, Field, field_validator

SITE_ID_PATTERN = r"^site[0-9]+$"


@functools.cache
def _iana_zones() -> frozenset[str]:
    return frozenset(zoneinfo.available_timezones())


def check_timezone(value: str) -> str:
    """Return `value` if it names a zone in the IANA database; raise ValueError otherwise."""
    if value not in _iana_zones():
        raise ValueError("not a time zone in the IANA database")
    return value


# @spec DATA-MODEL-001, DATA-ID-001, DATA-SITE-002
class Site(BaseModel):
    """A fixed recording location, with generalized coordinates only."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    site_id: str = Field(pattern=SITE_ID_PATTERN)
    latitude_generalized_deg: float
    longitude_generalized_deg: float
    coordinate_uncertainty_m: int
    generalization: str
    habitat: str | None = None
    timezone: str
    nws_station_id: str | None = None

    @field_validator("timezone")
    @classmethod
    def _timezone_is_iana(cls, value: str) -> str:
        return check_timezone(value)
