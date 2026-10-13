"""The Site model and how configuration loading generalizes site coordinates."""

from __future__ import annotations

import pydantic
import pytest

from tests.conftest import SITES_TOML, Project
from urbanbio import config
from urbanbio.models import site as site_module

FIELDS = {
    "site_id",
    "latitude_generalized_deg",
    "longitude_generalized_deg",
    "coordinate_uncertainty_m",
    "generalization",
    "habitat",
    "timezone",
    "nws_station_id",
}


def make(**overrides):
    values = {
        "site_id": "site1",
        "latitude_generalized_deg": 12.35,
        "longitude_generalized_deg": -45.68,
        "coordinate_uncertainty_m": 1000,
        "generalization": "rounded to 0.01°",
        "habitat": None,
        "timezone": "Etc/UTC",
        "nws_station_id": None,
    }
    values.update(overrides)
    return site_module.Site(**values)


# --- The model --------------------------------------------------------------------------


# @spec DATA-MODEL-001
def test_site_has_exactly_the_listed_fields() -> None:
    assert set(site_module.Site.model_fields) == FIELDS
    for name in ("habitat", "nws_station_id"):
        assert not site_module.Site.model_fields[name].is_required()


# @spec DATA-MODEL-001
def test_site_is_frozen_and_forbids_extra_fields() -> None:
    site = make()
    with pytest.raises(pydantic.ValidationError):
        site.site_id = "site2"  # type: ignore[misc]
    with pytest.raises(pydantic.ValidationError):
        make(latitude_exact=12.3456789)


# @spec DATA-ID-001
@pytest.mark.parametrize("site_id", ["Site1", "site", "site-1", "s1", "site1a", "lab-roof"])
def test_site_id_must_be_opaque(site_id: str) -> None:
    with pytest.raises(pydantic.ValidationError):
        make(site_id=site_id)


# @spec DATA-SITE-002
@pytest.mark.parametrize("timezone", ["Nowhere/Nothing", "EST5EDT6", "", "utc+1"])
def test_timezone_must_be_iana(timezone: str) -> None:
    with pytest.raises(pydantic.ValidationError):
        make(timezone=timezone)


# @spec DATA-SITE-002
@pytest.mark.parametrize("timezone", ["Etc/UTC", "America/New_York", "Europe/Berlin"])
def test_iana_timezones_are_accepted(timezone: str) -> None:
    assert make(timezone=timezone).timezone == timezone


# --- Generalization at load -------------------------------------------------------------


# @spec DATA-SITE-001
@pytest.mark.parametrize(
    ("precision", "lat", "lon", "uncertainty", "rule"),
    [
        (None, 12.35, -45.68, 1000, "rounded to 0.01°"),
        ("0.01", 12.35, -45.68, 1000, "rounded to 0.01°"),
        ("0.05", 12.35, -45.7, 5000, "rounded to 0.05°"),
        ("0.1", 12.3, -45.7, 10000, "rounded to 0.1°"),
        ("0.25", 12.25, -45.75, 25000, "rounded to 0.25°"),
        ("1", 12.0, -46.0, 100000, "rounded to 1°"),
    ],
)
def test_generalization_uses_the_site_precision(
    processing: Project,
    precision: str | None,
    lat: float,
    lon: float,
    uncertainty: int,
    rule: str,
) -> None:
    sites = SITES_TOML
    if precision is not None:
        sites += f"coordinate_precision_deg = {precision}\n"
    processing.monkeypatch.setenv("URBANBIO_SITES", sites)
    (site,) = config.load(with_sites=True).sites
    assert site.latitude_generalized_deg == lat
    assert site.longitude_generalized_deg == lon
    assert site.coordinate_uncertainty_m == uncertainty
    assert site.generalization == rule
    # No more decimal places than the precision: exact float equality above, and a short repr.
    assert len(repr(site.latitude_generalized_deg).split(".")[-1]) <= 2


# @spec DATA-SITE-001
def test_generalization_follows_the_publish_settings(processing: Project) -> None:
    pipeline = processing.root / "config" / "pipeline.toml"
    pipeline.write_text(
        pipeline.read_text()
        .replace("coordinate_precision_deg = 0.01", "coordinate_precision_deg = 0.02")
        .replace("coordinate_uncertainty_m = 1000", "coordinate_uncertainty_m = 2000")
    )
    processing.monkeypatch.setenv("URBANBIO_SITES", SITES_TOML + "coordinate_precision_deg = 0.1\n")
    (site,) = config.load(with_sites=True).sites
    assert site.latitude_generalized_deg == 12.3
    assert site.coordinate_uncertainty_m == 10000
    assert site.generalization == "rounded to 0.1°"


# @spec DATA-SITE-001
@pytest.mark.parametrize(("exact", "expected"), [(0.004, 0.0), (-0.006, -0.01), (89.996, 90.0)])
def test_rounding_is_to_the_nearest_multiple(
    processing: Project, exact: float, expected: float
) -> None:
    sites = SITES_TOML.replace("12.3456789", str(exact))
    processing.monkeypatch.setenv("URBANBIO_SITES", sites)
    (site,) = config.load(with_sites=True).sites
    assert site.latitude_generalized_deg == expected
