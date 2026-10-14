"""The store marker, `urbanbio store init`, `open_store`, and changing stores.

Store LLD § Store Marker, § Same-Filesystem Rule, § Changing Stores.
"""

from __future__ import annotations

import json
import logging
import re
import shutil
import tempfile
from collections.abc import Iterator
from datetime import datetime
from pathlib import Path
from typing import Any

import pytest

from tests.conftest import BUCKET, MARKER_KEY, AwsCalls, Project, store_init
from urbanbio import config
from urbanbio.errors import ConfigError
from urbanbio.storage.store import open_store

EXIT_CONFIG = 3


def opened() -> Any:
    return open_store(config.load(with_sites=False))


def marker(store: Any, tmp_path: Path) -> dict[str, Any]:
    dest = tmp_path / "marker.json"
    store.get(MARKER_KEY, dest)
    return json.loads(dest.read_text())


def dev_root(project: Project) -> Path:
    return (project.home / "urbanbio-dev" / "store").resolve()


def processing_on_filesystem(project: Project, root: Path) -> None:
    """Switch the project's processing profile to a `filesystem` store at `root`."""
    project.write(
        "config/processing.toml",
        (project.root / "config" / "processing.toml").read_text().split("[store]")[0]
        + f'[store]\nbackend = "filesystem"\nroot = "{root}"\n',
    )


@pytest.fixture
def other_filesystem_dir(fs_env: Project) -> Iterator[Path]:
    """A writable directory on a different filesystem from the test's home directory."""
    home_dev = fs_env.home.stat().st_dev
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


# --- store init -----------------------------------------------------------------------------


# @spec STORE-MARK-001
def test_init_writes_the_marker(backend: str, tmp_path: Path) -> None:
    result = store_init()
    assert result.exit_code == 0, result.output
    data = marker(opened(), tmp_path)
    assert data["schema"] == "urbanbio.store-marker/1"
    assert re.fullmatch(r"st-[0-9a-f]{16}", data["store_id"])
    assert data["profile"] == ("dev" if backend == "filesystem" else "processing")
    assert data["created_utc"].endswith("Z")
    datetime.fromisoformat(data["created_utc"])
    assert set(data) == {"schema", "store_id", "profile", "created_utc"}


# @spec STORE-MARK-001
def test_each_store_gets_a_random_id(fs_env: Project, tmp_path: Path) -> None:
    assert store_init().exit_code == 0
    first = marker(opened(), tmp_path)["store_id"]
    root = dev_root(fs_env)
    shutil.rmtree(root)
    root.mkdir()
    (fs_env.home / "urbanbio-dev" / "state" / "store-id").unlink(missing_ok=True)
    assert store_init().exit_code == 0
    assert marker(opened(), tmp_path)["store_id"] != first


# @spec STORE-MARK-002
def test_init_refuses_a_store_that_has_a_marker(backend: str, tmp_path: Path) -> None:
    assert store_init().exit_code == 0
    before = marker(opened(), tmp_path)
    result = store_init()
    assert result.exit_code == EXIT_CONFIG
    assert marker(opened(), tmp_path) == before


# @spec STORE-MARK-002
def test_init_refuses_a_marker_of_another_profile(s3_env: Project, aws: AwsCalls) -> None:
    assert store_init().exit_code == 0
    body = aws.raw.get_object(Bucket=BUCKET, Key=MARKER_KEY)["Body"].read()
    _use_s3_in_dev(s3_env)
    assert store_init().exit_code == EXIT_CONFIG
    assert aws.raw.get_object(Bucket=BUCKET, Key=MARKER_KEY)["Body"].read() == body


# @spec STORE-MARK-002
def test_init_refuses_a_store_that_holds_any_object(s3_env: Project, aws: AwsCalls) -> None:
    aws.raw.put_object(Bucket=BUCKET, Key="runs/someone-elses.json", Body=b"{}")
    assert store_init().exit_code == EXIT_CONFIG
    listed = aws.raw.list_objects_v2(Bucket=BUCKET).get("Contents", [])
    assert [o["Key"] for o in listed] == ["runs/someone-elses.json"]


# @spec STORE-MARK-003
def test_init_never_creates_a_missing_root(dev: Project) -> None:
    dev.make_data_dirs()
    root = dev.home / "urbanbio-dev" / "store"
    assert store_init().exit_code == EXIT_CONFIG
    assert not root.exists()


# @spec STORE-MARK-004
def test_processing_init_refuses_a_root_on_the_quarantine_filesystem(processing: Project) -> None:
    root = processing.home / "share"
    root.mkdir()
    processing_on_filesystem(processing, root)
    processing.make_data_dirs()
    assert store_init().exit_code == EXIT_CONFIG
    assert list(root.iterdir()) == []


# @spec STORE-MARK-004, STORE-SAME-001
def test_processing_init_accepts_a_root_on_another_filesystem(
    fs_env: Project, other_filesystem_dir: Path, tmp_path: Path
) -> None:
    fs_env.profile("processing")
    processing_on_filesystem(fs_env, other_filesystem_dir)
    fs_env.make_data_dirs()
    result = store_init()
    assert result.exit_code == 0, result.output
    assert marker(opened(), tmp_path)["profile"] == "processing"


# --- open_store -----------------------------------------------------------------------------


# @spec STORE-MARK-006
def test_open_without_a_marker_names_store_init(backend: str) -> None:
    with pytest.raises(ConfigError) as caught:
        opened()
    assert caught.value.reason_code == "store_marker_missing"
    assert "urbanbio store init" in str(caught.value)
    if backend == "filesystem":
        assert "unmounted" in str(caught.value)


# @spec STORE-MARK-007
def test_open_refuses_a_marker_of_another_profile(fs_env: Project) -> None:
    assert store_init().exit_code == 0
    fs_env.profile("processing")
    processing_on_filesystem(fs_env, dev_root(fs_env))
    fs_env.make_data_dirs()
    with pytest.raises(ConfigError) as caught:
        opened()
    assert caught.value.reason_code == "store_profile_mismatch"


# @spec STORE-MARK-007
def test_open_refuses_a_processing_store_from_dev(s3_env: Project) -> None:
    assert store_init().exit_code == 0
    _use_s3_in_dev(s3_env)
    with pytest.raises(ConfigError) as caught:
        opened()
    assert caught.value.reason_code == "store_profile_mismatch"


def _use_s3_in_dev(project: Project) -> None:
    project.profile("dev")
    project.monkeypatch.delenv("URBANBIO_SITES")
    project.operator_config('[store]\nbackend = "s3"\nregion = "us-east-1"\n')
    project.make_data_dirs()


# --- Changing stores ------------------------------------------------------------------------


def state_file(project: Project) -> Path:
    return project.home / "urbanbio-dev" / "state" / "store-id"


def quarantine(project: Project) -> Path:
    return project.home / "urbanbio-dev" / "quarantine"


# @spec STORE-SWITCH-001
def test_open_records_the_store_id(fs_env: Project, tmp_path: Path) -> None:
    assert store_init().exit_code == 0
    store = opened()
    store_id = marker(store, tmp_path)["store_id"]
    assert state_file(fs_env).read_text().strip() == store_id
    opened()
    assert state_file(fs_env).read_text().strip() == store_id


# @spec STORE-SWITCH-002
def test_a_new_store_id_with_retrievals_in_flight_is_refused(fs_env: Project) -> None:
    assert store_init().exit_code == 0
    state_file(fs_env).write_text("st-0000000000000000\n")
    (quarantine(fs_env) / "site7-dep001-r002" / "card" / "part01").mkdir(parents=True)
    (quarantine(fs_env) / "incoming" / "card-a").mkdir(parents=True)
    with pytest.raises(ConfigError) as caught:
        opened()
    message = str(caught.value)
    assert caught.value.reason_code == "store_changed"
    assert "st-0000000000000000" in message
    assert re.search(r"st-[0-9a-f]{16}", message.replace("st-0000000000000000", ""))
    assert "site7-dep001-r002" in message
    assert state_file(fs_env).read_text().strip() == "st-0000000000000000"


# @spec STORE-SWITCH-003
def test_a_new_store_id_with_nothing_in_flight_warns_and_continues(
    fs_env: Project, tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    assert store_init().exit_code == 0
    state_file(fs_env).write_text("st-0000000000000000\n")
    (quarantine(fs_env) / "incoming" / "card-a").mkdir(parents=True)
    (quarantine(fs_env) / "speech-labeling").mkdir()
    with caplog.at_level(logging.WARNING, logger="urbanbio"):
        store = opened()
    store_id = marker(store, tmp_path)["store_id"]
    warnings = [r.getMessage() for r in caplog.records if r.levelno == logging.WARNING]
    assert any("st-0000000000000000" in w and store_id in w for w in warnings)
    assert state_file(fs_env).read_text().strip() == store_id
