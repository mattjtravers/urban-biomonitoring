"""`urbanbio config check`, exercised through `config.check()` and the CLI.

`config.check()` returns one result per check, each with a `name`, a `status` (`ok`, `fail`, or
`warn`), a `message`, and a `fix`. Most tests here have no store, so the `store` check fails in
them; each test asserts only on the checks it targets. Any test that sets AWS credentials runs
under the `aws` fixture (moto), so no check ever reaches AWS.
"""

from __future__ import annotations

import os
import shutil
import tempfile
from collections.abc import Iterator
from pathlib import Path

import pytest
from botocore.exceptions import EndpointConnectionError
from typer.testing import CliRunner

from tests.conftest import (
    BUCKET,
    EXACT_DIGITS,
    MARKER_KEY,
    SITES_TOML,
    AwsCalls,
    Project,
    store_init,
)
from urbanbio import cli, config


def statuses() -> dict[str, str]:
    return {r.name: r.status for r in config.check()}


def results() -> dict[str, config.CheckResult]:
    return {r.name: r for r in config.check()}


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
def test_all_required_variables_present_passes(processing: Project, aws: AwsCalls) -> None:
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
def test_check_never_prints_private_values(processing: Project, aws: AwsCalls) -> None:
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


# --- Store ----------------------------------------------------------------------------------


def store_files(root: Path) -> list[str]:
    return sorted(p.relative_to(root).as_posix() for p in root.rglob("*"))


# @spec CLI-CHECK-006
def test_an_initialized_filesystem_store_passes(fs_env: Project) -> None:
    assert store_init().exit_code == 0
    root = fs_env.home / "urbanbio-dev" / "store"
    before = store_files(root)
    assert statuses()["store"] == "ok"
    assert store_files(root) == before


# @spec CLI-CHECK-006
def test_a_filesystem_store_without_a_marker_fails(fs_env: Project) -> None:
    result = results()["store"]
    assert result.status == "fail"
    assert "urbanbio store init" in result.message + result.fix
    assert list((fs_env.home / "urbanbio-dev" / "store").iterdir()) == []


# @spec CLI-CHECK-006
def test_a_missing_filesystem_root_fails(fs_env: Project) -> None:
    (fs_env.home / "urbanbio-dev" / "store").rmdir()
    assert statuses()["store"] == "fail"


# @spec CLI-CHECK-006
def test_a_store_of_another_profile_fails(fs_env: Project) -> None:
    assert store_init().exit_code == 0
    fs_env.profile("processing")
    fs_env.monkeypatch.setenv("URBANBIO_SITES", SITES_TOML)
    root = fs_env.home / "urbanbio-dev" / "store"
    fs_env.write(
        "config/processing.toml",
        (fs_env.root / "config" / "processing.toml").read_text().split("[store]")[0]
        + f'[store]\nbackend = "filesystem"\nroot = "{root}"\n',
    )
    fs_env.make_data_dirs()
    result = results()["store"]
    assert result.status == "fail"
    assert "processing" in result.message or "profile" in result.message


# @spec CLI-CHECK-006
def test_an_initialized_s3_store_passes_and_writes_nothing(s3_env: Project, aws: AwsCalls) -> None:
    assert store_init().exit_code == 0
    aws.calls.clear()
    assert statuses()["store"] == "ok"
    assert aws.calls
    writes = {"PutObject", "DeleteObject", "CopyObject", "CreateMultipartUpload"}
    assert not writes & set(aws.ops())


# @spec CLI-CHECK-006
def test_an_s3_store_without_a_marker_fails(s3_env: Project, aws: AwsCalls) -> None:
    result = results()["store"]
    assert result.status == "fail"
    assert "urbanbio store init" in result.message + result.fix
    listed = aws.raw.list_objects_v2(Bucket=BUCKET).get("Contents", [])
    assert MARKER_KEY not in [o["Key"] for o in listed]


# @spec CLI-CHECK-006, CLI-CHECK-009
def test_an_unreachable_s3_store_fails_without_naming_the_bucket(
    s3_env: Project, aws: AwsCalls
) -> None:
    assert store_init().exit_code == 0

    def unreachable(params: dict) -> None:
        raise EndpointConnectionError(endpoint_url="https://s3.us-east-1.amazonaws.com")

    for op in ("GetObject", "HeadObject", "ListObjectsV2"):
        aws.before[op] = unreachable
    result = results()["store"]
    assert result.status == "fail"
    assert BUCKET not in result.message + result.fix


# @spec CLI-CHECK-011
@pytest.mark.parametrize("var", ["URBANBIO_BUCKET", "AWS_ACCESS_KEY_ID", "AWS_SECRET_ACCESS_KEY"])
def test_store_isnt_contacted_without_the_private_variables(
    s3_env: Project, aws: AwsCalls, var: str
) -> None:
    assert store_init().exit_code == 0
    aws.calls.clear()
    s3_env.monkeypatch.setenv(var, "")
    result = results()["store"]
    assert result.status == "fail"
    assert "not checked" in result.message
    assert aws.calls == []


# @spec CLI-CHECK-011
def test_store_isnt_contacted_when_the_directories_fail(s3_env: Project, aws: AwsCalls) -> None:
    assert store_init().exit_code == 0
    aws.calls.clear()
    (s3_env.home / "urbanbio" / "state").chmod(0o755)
    result = results()["store"]
    assert result.status == "fail"
    assert "not checked" in result.message
    assert aws.calls == []


# @spec CLI-CHECK-011
def test_store_isnt_contacted_when_configuration_doesnt_load(fs_env: Project) -> None:
    assert store_init().exit_code == 0
    fs_env.operator_config("[screen]\npadding_s = 3.0\n")
    result = results()["store"]
    assert result.status == "fail"
    assert "not checked" in result.message


# --- Same filesystem ------------------------------------------------------------------------


def processing_on_filesystem(project: Project, root: Path) -> None:
    project.write(
        "config/processing.toml",
        (project.root / "config" / "processing.toml").read_text().split("[store]")[0]
        + f'[store]\nbackend = "filesystem"\nroot = "{root}"\n',
    )


@pytest.fixture
def other_filesystem_dir(project: Project) -> Iterator[Path]:
    home_dev = project.home.stat().st_dev
    for base in ("/dev/shm", "/run/user"):
        if Path(base).is_dir() and Path(base).stat().st_dev != home_dev:
            try:
                path = Path(tempfile.mkdtemp(dir=base))
            except OSError:
                continue
            yield path
            shutil.rmtree(path, ignore_errors=True)
            return
    pytest.skip("no writable directory on another filesystem")


# @spec CLI-CHECK-007
def test_processing_root_on_the_quarantine_filesystem_fails(processing: Project) -> None:
    root = processing.home / "share"
    root.mkdir()
    processing_on_filesystem(processing, root)
    processing.make_data_dirs()
    result = results()["same_filesystem"]
    assert result.status == "fail"
    assert "paths.quarantine" in result.message


# @spec CLI-CHECK-007
def test_processing_root_on_another_filesystem_passes(
    processing: Project, other_filesystem_dir: Path
) -> None:
    processing_on_filesystem(processing, other_filesystem_dir)
    processing.make_data_dirs()
    assert store_init().exit_code == 0
    result = results()
    assert result["same_filesystem"].status == "ok"
    assert result["store"].status == "ok"


# @spec CLI-CHECK-007
def test_dev_doesnt_apply_the_same_filesystem_rule(fs_env: Project) -> None:
    assert store_init().exit_code == 0
    assert statuses().get("same_filesystem", "ok") != "fail"
