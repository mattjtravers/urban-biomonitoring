"""Shared fixtures: an isolated repository working tree, home directory, and environment."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

import pytest

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
