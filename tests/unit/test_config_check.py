"""`urbanbio config check`, exercised through `config.check()` and the CLI.

`config.check()` returns one result per check, each with a `name`, a `status` (`ok`, `fail`, or
`warn`), a `message`, and a `fix`. Tests here run without a store, so the `store` check fails in
every one of them; each test asserts only on the checks it targets.
"""

from __future__ import annotations

import os

import pytest
from typer.testing import CliRunner

from tests.conftest import EXACT_DIGITS, SITES_TOML, Project
from urbanbio import cli, config


def statuses() -> dict[str, str]:
    return {r.name: r.status for r in config.check()}


# @spec CLI-CHECK-001
def test_unset_profile_fails(project: Project) -> None:
    assert statuses()["profile"] == "fail"


# @spec CLI-CHECK-001
def test_unknown_profile_fails(project: Project) -> None:
    project.profile("staging")
    assert statuses()["profile"] == "fail"


# @spec CLI-CHECK-001
def test_known_profile_passes(processing: Project) -> None:
    assert statuses()["profile"] == "ok"


# @spec CLI-CHECK-001
def test_layer_violation_fails(processing: Project) -> None:
    processing.operator_config("[screen]\npadding_s = 3.0\n")
    assert statuses()["layers"] == "fail"


# @spec CLI-CHECK-001
@pytest.mark.parametrize("kind", ["relative", "missing", "inside"])
def test_bad_operator_config_fails(processing: Project, kind: str) -> None:
    value = {
        "relative": "config.toml",
        "missing": str(processing.home / "missing.toml"),
        "inside": str(processing.write("operator.toml", "[resources]\nworkers = 1\n")),
    }[kind]
    processing.monkeypatch.setenv("URBANBIO_CONFIG", value)
    assert statuses()["operator_config"] == "fail"


# @spec CLI-CHECK-002
@pytest.mark.parametrize("var", ["URBANBIO_BUCKET", "AWS_ACCESS_KEY_ID", "AWS_SECRET_ACCESS_KEY"])
@pytest.mark.parametrize("state", ["missing", "empty"])
def test_s3_backend_requires_bucket_and_keys(processing: Project, var: str, state: str) -> None:
    for name in ("URBANBIO_BUCKET", "AWS_ACCESS_KEY_ID", "AWS_SECRET_ACCESS_KEY"):
        processing.monkeypatch.setenv(name, "value")
    if state == "missing":
        processing.monkeypatch.delenv(var)
    else:
        processing.monkeypatch.setenv(var, "")
    assert statuses()["private_vars"] == "fail"


# @spec CLI-CHECK-002
@pytest.mark.parametrize("state", ["missing", "empty"])
def test_processing_requires_sites(processing: Project, state: str) -> None:
    for name in ("URBANBIO_BUCKET", "AWS_ACCESS_KEY_ID", "AWS_SECRET_ACCESS_KEY"):
        processing.monkeypatch.setenv(name, "value")
    if state == "missing":
        processing.monkeypatch.delenv("URBANBIO_SITES")
    else:
        processing.monkeypatch.setenv("URBANBIO_SITES", "")
    assert statuses()["private_vars"] == "fail"


# @spec CLI-CHECK-002
def test_all_required_variables_present_passes(processing: Project) -> None:
    for name in ("URBANBIO_BUCKET", "AWS_ACCESS_KEY_ID", "AWS_SECRET_ACCESS_KEY"):
        processing.monkeypatch.setenv(name, "value")
    assert statuses()["private_vars"] == "ok"


# @spec CLI-CHECK-002
def test_dev_filesystem_store_requires_no_private_variable(dev: Project) -> None:
    assert statuses()["private_vars"] == "ok"


# @spec CLI-CHECK-003
@pytest.mark.parametrize(
    "text",
    [
        "not toml [[[",
        SITES_TOML.replace('"America/New_York"', '"Nowhere/Nothing"'),
        SITES_TOML + "coordinate_precision_deg = 0.001\n",
    ],
    ids=["unparseable", "unknown-timezone", "precision-too-fine"],
)
def test_bad_site_configuration_fails(processing: Project, text: str) -> None:
    processing.monkeypatch.setenv("URBANBIO_SITES", text)
    assert statuses()["sites"] == "fail"


# @spec CLI-CHECK-003
def test_good_site_configuration_passes(processing: Project) -> None:
    assert statuses()["sites"] == "ok"


# @spec CLI-CHECK-004
@pytest.mark.parametrize(
    ("lat", "lon", "expected"),
    [
        ("12.3456", "-45.68", "fail"),  # latitude finer than 0.01°
        ("12.35", "-45.6789", "fail"),  # longitude finer than 0.01°
        ("12.35", "-45.68", "ok"),  # already generalized
        ("12", "-45", "ok"),  # integers are multiples
    ],
)
def test_coordinate_like_pair_in_a_layer(
    processing: Project, lat: str, lon: str, expected: str
) -> None:
    processing.operator_config(f"[paths]\nsite_Lat = {lat}\nLONGITUDE_hint = {lon}\n")
    assert statuses()["coordinate_like"] == expected


# @spec CLI-CHECK-004
def test_coordinate_like_pair_in_the_pipeline_file(processing: Project) -> None:
    pipeline = processing.root / "config" / "pipeline.toml"
    pipeline.write_text(pipeline.read_text() + "\n[detect.geo]\nlat = 40.71234\nlon = -74.0\n")
    assert statuses()["coordinate_like"] == "fail"


# @spec CLI-CHECK-004
def test_lat_and_lon_in_different_tables_are_not_a_pair(processing: Project) -> None:
    processing.operator_config("[paths]\nlat_x = 12.3456\n\n[resources]\nlon_x = -45.6789\n")
    assert statuses()["coordinate_like"] == "ok"


# @spec CLI-CHECK-004
def test_default_layers_have_no_coordinate_like_pair(processing: Project) -> None:
    assert statuses()["coordinate_like"] == "ok"


# @spec CLI-CHECK-005
def test_path_inside_the_working_tree_fails(processing: Project) -> None:
    processing.operator_config(f'[paths]\nwork = "{processing.root / "scratch"}"\n')
    assert statuses()["paths_outside_repo"] == "fail"


# @spec CLI-CHECK-005
def test_missing_data_directories_fail(processing: Project) -> None:
    assert statuses()["directories"] == "fail"


# @spec CLI-CHECK-005
@pytest.mark.parametrize("name", ["quarantine", "work", "state"])
def test_data_directory_with_wrong_mode_fails(processing: Project, name: str) -> None:
    processing.make_data_dirs()
    (processing.home / "urbanbio" / name).chmod(0o755)
    assert statuses()["directories"] == "fail"


# @spec CLI-CHECK-005
def test_private_data_directories_pass(processing: Project) -> None:
    processing.make_data_dirs()
    result = statuses()
    assert result["directories"] == "ok"
    assert result["paths_outside_repo"] == "ok"


# @spec CLI-CHECK-008
def test_dev_with_private_sites_fails(dev: Project) -> None:
    dev.monkeypatch.setenv("URBANBIO_SITES", SITES_TOML)
    assert statuses()["dev_private_sites"] == "fail"


# @spec CLI-CHECK-008
def test_dev_without_private_sites_passes(dev: Project) -> None:
    assert statuses()["dev_private_sites"] == "ok"


# @spec CLI-CHECK-009
def test_failing_check_prints_check_and_fix_and_exits_3(processing: Project) -> None:
    result = CliRunner().invoke(cli.app, ["config", "check"])
    assert result.exit_code == 3
    failures = [r for r in config.check() if r.status == "fail"]
    assert failures
    for failure in failures:
        assert failure.fix
        assert failure.name in result.output
        assert failure.fix in result.output


# @spec CLI-CHECK-009
def test_check_never_prints_private_values(processing: Project) -> None:
    secrets = {
        "URBANBIO_BUCKET": "bucket-secret-7f3a",
        "AWS_ACCESS_KEY_ID": "AKIATESTKEYID000",
        "AWS_SECRET_ACCESS_KEY": "aws-secret-value-123",
    }
    for var, value in secrets.items():
        processing.monkeypatch.setenv(var, value)
    # A broken site configuration whose bad line holds the exact coordinates.
    processing.monkeypatch.setenv("URBANBIO_SITES", SITES_TOML.replace("timezone", "time zone"))
    result = CliRunner().invoke(cli.app, ["config", "check"])
    assert result.exit_code == 3
    for value in secrets.values():
        assert value not in result.output
    for digits in EXACT_DIGITS:
        assert digits not in result.output


# @spec CLI-CHECK-010
def test_more_workers_than_cpus_warns_without_failing(processing: Project) -> None:
    workers = (os.cpu_count() or 1) + 1
    processing.operator_config(f"[resources]\nworkers = {workers}\n")
    assert statuses()["workers"] == "warn"


# @spec CLI-CHECK-010
def test_workers_within_cpu_count_pass(processing: Project) -> None:
    processing.operator_config("[resources]\nworkers = 1\n")
    assert statuses()["workers"] == "ok"
