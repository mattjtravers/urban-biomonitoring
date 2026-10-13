"""Private values: where they come from, and how exact coordinates stay out of reach."""

from __future__ import annotations

import inspect
import math
from typing import Any

import pytest

from tests.conftest import EXACT_DIGITS, EXACT_LAT, EXACT_LON, SITES_TOML, Project
from urbanbio import config, errors


def _walk(value: Any, seen: set[int] | None = None):
    """Yield every value reachable from a config object through attributes and containers."""
    seen = set() if seen is None else seen
    if id(value) in seen:
        return
    seen.add(id(value))
    yield value
    if isinstance(value, dict):
        for item in value.values():
            yield from _walk(item, seen)
    elif isinstance(value, list | tuple | set | frozenset):
        for item in value:
            yield from _walk(item, seen)
    elif hasattr(value, "__dict__") and not isinstance(value, type):
        for item in vars(value).values():
            yield from _walk(item, seen)


# @spec CLI-PRIV-001
@pytest.mark.parametrize(
    "relpath",
    ["config/private/sites.toml", "config/sites.local.toml", "config/sites.toml", ".env"],
)
def test_sites_are_never_read_from_a_file(processing: Project, relpath: str) -> None:
    processing.monkeypatch.delenv("URBANBIO_SITES")
    processing.write(relpath, SITES_TOML)
    home_copy = processing.home / ".config" / "urbanbio" / "sites.toml"
    home_copy.parent.mkdir(parents=True, exist_ok=True)
    home_copy.write_text(SITES_TOML)
    with pytest.raises(errors.ConfigError):
        config.load(with_sites=True)


# @spec CLI-PRIV-002
def test_sites_parse_from_the_environment(processing: Project) -> None:
    cfg = config.load(with_sites=True)
    (site,) = cfg.sites
    assert site.site_id == "site1"
    assert site.timezone == "America/New_York"
    assert site.habitat == "test habitat"
    assert site.nws_station_id == "TEST"


# @spec CLI-PRIV-002
def test_optional_site_fields_may_be_omitted(processing: Project) -> None:
    minimal = (
        '[[site]]\nsite_id = "site2"\nlatitude_exact = 1.0\nlongitude_exact = 2.0\n'
        'timezone = "Etc/UTC"\n'
    )
    processing.monkeypatch.setenv("URBANBIO_SITES", minimal)
    (site,) = config.load(with_sites=True).sites
    assert site.habitat is None
    assert site.nws_station_id is None


# @spec CLI-PRIV-002
@pytest.mark.parametrize(
    "text",
    [
        "not toml [[[",
        '[[site]]\nsite_id = "site1"\nlongitude_exact = 1.0\ntimezone = "Etc/UTC"\n',
        '[[site]]\nsite_id = "site1"\nlatitude_exact = 1.0\nlongitude_exact = 1.0\n'
        'timezone = "Etc/UTC"\naddress = "x"\n',
        '[[site]]\nsite_id = "site1"\nlatitude_exact = 1.0\nlongitude_exact = 1.0\n'
        'timezone = "Mars/Olympus_Mons"\n',
    ],
    ids=["unparseable", "missing-latitude", "unknown-key", "unknown-timezone"],
)
def test_bad_site_configuration_is_refused(processing: Project, text: str) -> None:
    processing.monkeypatch.setenv("URBANBIO_SITES", text)
    with pytest.raises(errors.ConfigError):
        config.load(with_sites=True)


# @spec CLI-PRIV-003
def test_pipeline_config_cannot_reach_exact_coordinates(processing: Project) -> None:
    cfg = config.load(with_sites=True)
    for value in _walk(cfg):
        assert not isinstance(value, config.SiteSecret)
        if isinstance(value, float):
            assert value not in (EXACT_LAT, EXACT_LON)
    dumped = repr(cfg) + str(cfg.model_dump())
    for digits in EXACT_DIGITS:
        assert digits not in dumped


# @spec CLI-PRIV-003
def test_sites_carry_generalized_coordinates(processing: Project) -> None:
    (site,) = config.load(with_sites=True).sites
    assert site.latitude_generalized_deg == pytest.approx(12.35)
    assert site.longitude_generalized_deg == pytest.approx(-45.68)
    assert not any("exact" in name for name in type(site).model_fields)


# @spec CLI-PRIV-004
def test_site_offset_is_distance_from_the_exact_position(processing: Project) -> None:
    config.load(with_sites=True)
    assert config.site_offset_m("site1", EXACT_LAT, EXACT_LON) == pytest.approx(0.0, abs=1e-6)
    # 0.01° of latitude is about 1112 m anywhere on the sphere.
    north = config.site_offset_m("site1", EXACT_LAT + 0.01, EXACT_LON)
    assert north == pytest.approx(1112.0, abs=2.0)
    # From the generalized point, the offset is that of the exact point, not zero.
    generalized = config.site_offset_m("site1", 12.35, -45.68)
    expected = math.hypot(
        (12.35 - EXACT_LAT) * 111_195.0,
        (-45.68 - EXACT_LON) * 111_195.0 * math.cos(math.radians(EXACT_LAT)),
    )
    assert generalized == pytest.approx(expected, rel=1e-2)
    assert generalized > 1.0


# @spec CLI-PRIV-005
def test_config_module_exposes_no_coordinate_function() -> None:
    """The only public functions of urbanbio.config are the reviewed ones.

    Adding a public function here is a privacy decision: it must not return a coordinate
    derived from exact ones (CLI-PRIV-005).
    """
    public_functions = {
        name
        for name, member in inspect.getmembers(config, inspect.isfunction)
        if not name.startswith("_") and member.__module__ == config.__name__
    }
    assert public_functions == {"load", "repo_root", "site_offset_m", "check"}


# @spec CLI-PRIV-005
def test_site_offset_returns_a_distance_only(processing: Project) -> None:
    config.load(with_sites=True)
    assert type(config.site_offset_m("site1", 0.0, 0.0)) is float


# @spec CLI-PRIV-006
def test_site_secret_masks_coordinates() -> None:
    secret = config.SiteSecret(
        site_id="site1",
        latitude_exact=EXACT_LAT,
        longitude_exact=EXACT_LON,
        timezone="Etc/UTC",
    )
    for text in (repr(secret), str(secret), f"{secret}", f"{secret!r}"):
        assert "site1" in text
        for digits in EXACT_DIGITS:
            assert digits not in text


# @spec CLI-PRIV-006
def test_traceback_through_load_does_not_print_coordinates(processing: Project) -> None:
    bad = SITES_TOML.replace('"America/New_York"', '"Nowhere/Nothing"')
    processing.monkeypatch.setenv("URBANBIO_SITES", bad)
    with pytest.raises(errors.ConfigError) as excinfo:
        config.load(with_sites=True)
    rendered = str(excinfo.getrepr(style="long")) + repr(excinfo.value)
    for digits in EXACT_DIGITS:
        assert digits not in rendered


# @spec CLI-PRIV-007
def test_loading_without_sites_needs_no_private_values(processing: Project) -> None:
    processing.monkeypatch.delenv("URBANBIO_SITES")
    cfg = config.load(with_sites=False)
    assert cfg.sites is None


# @spec CLI-PRIV-007
def test_loading_sites_in_processing_requires_the_variable(processing: Project) -> None:
    processing.monkeypatch.delenv("URBANBIO_SITES")
    with pytest.raises(errors.ConfigError):
        config.load(with_sites=True)


# @spec CLI-PRIV-007
def test_loading_never_reads_store_credentials(processing: Project) -> None:
    # The s3 backend's bucket and keys are read when a store is opened, not at load: loading
    # works without them, and none of them ends up in the loaded configuration.
    cfg = config.load(with_sites=True)
    assert cfg.store.backend == "s3"
    secrets = {
        "URBANBIO_BUCKET": "bucket-secret-7f3a",
        "AWS_ACCESS_KEY_ID": "AKIATESTKEYID000",
        "AWS_SECRET_ACCESS_KEY": "aws-secret-value-123",
    }
    for var, value in secrets.items():
        processing.monkeypatch.setenv(var, value)
    cfg = config.load(with_sites=True)
    for value in _walk(cfg):
        if isinstance(value, str):
            assert value not in secrets.values()


# @spec CLI-PRIV-008
def test_site_precision_finer_than_publish_precision_is_refused(processing: Project) -> None:
    processing.monkeypatch.setenv(
        "URBANBIO_SITES", SITES_TOML + "coordinate_precision_deg = 0.001\n"
    )
    with pytest.raises(errors.ConfigError):
        config.load(with_sites=True)


# @spec CLI-PRIV-008
@pytest.mark.parametrize("precision", ["0.01", "0.05", "0.1"])
def test_site_precision_equal_or_coarser_is_accepted(processing: Project, precision: str) -> None:
    processing.monkeypatch.setenv(
        "URBANBIO_SITES", SITES_TOML + f"coordinate_precision_deg = {precision}\n"
    )
    assert config.load(with_sites=True).sites
