"""Shared fixtures: an isolated repository working tree, home directory, and environment."""

from __future__ import annotations

import base64
import hashlib
import os
from collections.abc import Callable, Iterator
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import boto3
import pytest
from botocore.client import BaseClient
from botocore.exceptions import ClientError
from moto import mock_aws
from typer.testing import Result

from urbanbio import config

# Every variable the software reads. Tests start with none of them set.
URBANBIO_VARS = (
    "URBANBIO_PROFILE",
    "URBANBIO_CONFIG",
    "URBANBIO_SITES",
    "URBANBIO_BUCKET",
)

PIPELINE_TOML = """\
[recording]
sample_rate_hz = 48000
duty_on_s = 60
duty_off_s = 240
max_file_length_s = 60

[ingest.songmeter_micro2]
low_battery_v = 4.2

[screen]
padding_s = 2.0
birdnet_mask_labels = ["Human vocal_Human vocal"]
birdnet_threshold = 0.10
birdnet_stats_floor = 0.02
birdnet_overlap_s = 1.5
silero_threshold = 0.30
silero_min_speech_ms = 250
max_masked_fraction = 0.20

[detect]
model = "birdnet-acoustic-2.4"
backend = "litert"
precision = "fp32"
confidence_floor = 0.10
top_k = "none"
overlap_s = 0.0
sigmoid_sensitivity = 1.0
bandpass_fmin_hz = 0
bandpass_fmax_hz = 15000
geo_model = "birdnet-geo-2.4"
geo_min_confidence = 0.03

[archive]
flac_compression_level = 0.6

[publish]
coordinate_precision_deg = 0.01
coordinate_uncertainty_m = 1000
"""

PROCESSING_TOML = """\
[paths]
quarantine = "~/urbanbio/quarantine"
work = "~/urbanbio/work"
state = "~/urbanbio/state"
model_cache = "~/.cache/urbanbio/birdnet"
test_audio_cache = "~/.cache/urbanbio/test-audio"

[resources]
workers = 2

[quarantine]
budget_gb = 35.0
min_free_gb = 3.0

[store]
backend = "s3"
region = "us-east-1"
glacier_transition_days = 30
"""

DEV_TOML = """\
[paths]
quarantine = "~/urbanbio-dev/quarantine"
work = "~/urbanbio-dev/work"
state = "~/urbanbio-dev/state"
model_cache = "~/.cache/urbanbio/birdnet"
test_audio_cache = "~/.cache/urbanbio/test-audio"

[resources]
workers = 1

[quarantine]
budget_gb = 5.0
min_free_gb = 3.0

[store]
backend = "filesystem"
root = "~/urbanbio-dev/store"
"""

DEV_SITES_TOML = """\
[[site]]
site_id = "site7"
latitude_exact = 0.0
longitude_exact = 0.0
timezone = "Etc/UTC"
habitat = "synthetic"
"""

# Fictional exact coordinates (open ocean), chosen so the exact digits are easy to spot in
# any output: 12.3456789, -45.6789123. Generalized to 0.01° they are 12.35, -45.68.
EXACT_LAT = 12.3456789
EXACT_LON = -45.6789123
EXACT_DIGITS = ("3456789", "6789123")

SITES_TOML = f"""\
[[site]]
site_id = "site1"
latitude_exact = {EXACT_LAT}
longitude_exact = {EXACT_LON}
timezone = "America/New_York"
habitat = "test habitat"
nws_station_id = "TEST"
"""


@dataclass
class Project:
    """A throwaway repository working tree and home directory for one test."""

    root: Path
    home: Path
    monkeypatch: pytest.MonkeyPatch

    def write(self, relpath: str, text: str) -> Path:
        path = self.root / relpath
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)
        return path

    def operator_config(self, text: str) -> Path:
        """Write an operator config file outside the working tree and select it."""
        path = self.home / ".config" / "urbanbio" / "config.toml"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)
        self.monkeypatch.setenv("URBANBIO_CONFIG", str(path))
        return path

    def profile(self, name: str) -> None:
        self.monkeypatch.setenv("URBANBIO_PROFILE", name)

    def make_data_dirs(self, mode: int = 0o700) -> None:
        """Create the quarantine, work, and state directories of the selected profile."""
        prefix = "urbanbio-dev" if os.environ.get("URBANBIO_PROFILE") == "dev" else "urbanbio"
        for name in ("quarantine", "work", "state"):
            path = self.home / prefix / name
            path.mkdir(parents=True, exist_ok=True)
            path.chmod(mode)


@pytest.fixture
def project(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Project:
    root = tmp_path / "repo"
    home = tmp_path / "home"
    root.mkdir()
    home.mkdir()
    for var in URBANBIO_VARS:
        monkeypatch.delenv(var, raising=False)
    for var in [v for v in os.environ if v.startswith("AWS_")]:
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setenv("HOME", str(home))
    # The loader finds the working tree through config.repo_root(); point it at the fake one.
    monkeypatch.setattr(config, "repo_root", lambda: root, raising=False)

    p = Project(root=root, home=home, monkeypatch=monkeypatch)
    p.write("config/pipeline.toml", PIPELINE_TOML)
    p.write("config/processing.toml", PROCESSING_TOML)
    p.write("config/dev.toml", DEV_TOML)
    p.write("config/dev-sites.toml", DEV_SITES_TOML)
    return p


@pytest.fixture
def processing(project: Project) -> Project:
    """The processing profile with site configuration in the environment."""
    project.profile("processing")
    project.monkeypatch.setenv("URBANBIO_SITES", SITES_TOML)
    return project


@pytest.fixture
def dev(project: Project) -> Project:
    project.profile("dev")
    return project


# --- Stores ---------------------------------------------------------------------------------

BUCKET = "test-bucket"
# Fictional credentials for moto. They belong to no AWS account.
AWS_KEY_ID = "AKIATESTPIPELINE0001"
AWS_SECRET_KEY = "test-secret-key-not-real"
MARKER_KEY = ".urbanbio-store.json"
BACKENDS = ("filesystem", "s3")


def sha256_hex(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha256_b64(data: bytes) -> str:
    return base64.b64encode(hashlib.sha256(data).digest()).decode()


@dataclass
class AwsCalls:
    """Every AWS API call the code under test makes, with hooks to fail or alter one.

    `raw` is a client for arranging state behind the store's back; its calls aren't recorded
    and skip the fidelity patches below.
    """

    raw: Any
    calls: list[tuple[str, dict[str, Any]]] = field(default_factory=list)
    clients: list[Any] = field(default_factory=list)
    before: dict[str, Callable[[dict[str, Any]], None]] = field(default_factory=dict)
    after: dict[str, Callable[[dict[str, Any], dict[str, Any]], dict[str, Any]]] = field(
        default_factory=dict
    )

    def ops(self, key: str | None = None) -> list[str]:
        return [op for op, params in self.calls if key is None or params.get("Key") == key]

    def params(self, op: str, key: str | None = None) -> list[dict[str, Any]]:
        return [p for o, p in self.calls if o == op and (key is None or p.get("Key") == key)]


def _body_bytes(body: Any) -> bytes:
    if isinstance(body, bytes | bytearray):
        return bytes(body)
    position = body.tell()
    data = body.read()
    body.seek(position)
    return data


@pytest.fixture
def aws(monkeypatch: pytest.MonkeyPatch) -> Iterator[AwsCalls]:
    """moto in place of AWS, with the bucket created and every client call recorded."""
    with mock_aws():
        raw = boto3.client(
            "s3",
            region_name="us-east-1",
            aws_access_key_id=AWS_KEY_ID,
            aws_secret_access_key=AWS_SECRET_KEY,
        )
        raw.create_bucket(Bucket=BUCKET)
        rec = AwsCalls(raw=raw)
        real = BaseClient._make_api_call

        def spy(client: Any, op: str, params: dict[str, Any]) -> dict[str, Any]:
            if client is rec.raw:
                return real(client, op, params)
            if client not in rec.clients:
                rec.clients.append(client)
            rec.calls.append((op, dict(params)))
            if op in rec.before:
                rec.before[op](params)
            # moto stores a PUT whose ChecksumSHA256 doesn't match the body; AWS rejects it.
            claimed = params.get("ChecksumSHA256") if op == "PutObject" else None
            if claimed is not None and sha256_b64(_body_bytes(params["Body"])) != claimed:
                raise ClientError(
                    {"Error": {"Code": "BadDigest", "Message": "checksum mismatch"}}, op
                )
            response = real(client, op, params)
            if op in rec.after:
                response = rec.after[op](params, response)
            return response

        monkeypatch.setattr(BaseClient, "_make_api_call", spy)
        yield rec


def store_init() -> Result:
    """Run `urbanbio store init` in-process."""
    from typer.testing import CliRunner

    from urbanbio import cli

    return CliRunner().invoke(cli.app, ["store", "init"])


@pytest.fixture
def fs_env(dev: Project) -> Project:
    """The dev profile with its data directories and an empty `filesystem` store root."""
    dev.make_data_dirs()
    (dev.home / "urbanbio-dev" / "store").mkdir(parents=True)
    return dev


@pytest.fixture
def s3_env(processing: Project, aws: AwsCalls) -> Project:
    """The processing profile on the `s3` backend, against moto."""
    processing.monkeypatch.setenv("URBANBIO_BUCKET", BUCKET)
    processing.monkeypatch.setenv("AWS_ACCESS_KEY_ID", AWS_KEY_ID)
    processing.monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", AWS_SECRET_KEY)
    processing.make_data_dirs()
    return processing


@pytest.fixture(params=BACKENDS)
def backend(request: pytest.FixtureRequest) -> str:
    request.getfixturevalue("fs_env" if request.param == "filesystem" else "s3_env")
    return request.param


@pytest.fixture
def store(backend: str) -> Any:
    """An initialized, opened store on each backend."""
    from urbanbio.storage.store import open_store

    result = store_init()
    assert result.exit_code == 0, result.output
    return open_store(config.load(with_sites=False))
