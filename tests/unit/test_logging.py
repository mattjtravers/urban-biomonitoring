"""Console and JSON-lines file logging, and redaction of private values.

`urbanbio.logging.configure_console(verbose=...)` sets up the console handler on the `urbanbio`
logger; `urbanbio.logging.run_log(state_dir, run_id, stage)` is a context manager that writes the
run's JSON-lines file while open and returns its path. Structured fields are passed with
`extra=`. `urbanbio.logging.set_path_roots(roots)` registers the configured paths, by name, that
local paths in log messages are rewritten against.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timedelta
from pathlib import Path

import pytest

from tests.conftest import EXACT_DIGITS, SITES_TOML
from urbanbio import logging as ulog

log = logging.getLogger("urbanbio.tests")


@pytest.fixture(autouse=True)
def _reset_urbanbio_logger():
    logger = logging.getLogger("urbanbio")
    handlers, filters, level = list(logger.handlers), list(logger.filters), logger.level
    yield
    logger.handlers[:] = handlers
    logger.filters[:] = filters
    logger.setLevel(level)
    if hasattr(ulog, "set_path_roots"):
        ulog.set_path_roots({})


def read_lines(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text().splitlines()]


# @spec CLI-LOG-001
def test_console_logs_info_by_default(capsys: pytest.CaptureFixture[str]) -> None:
    ulog.configure_console(verbose=False)
    log.debug("debug detail")
    log.info("retrieval registered")
    err = capsys.readouterr().err
    assert "retrieval registered" in err
    assert "debug detail" not in err
    assert not err.lstrip().startswith("{")  # human-readable, not JSON


# @spec CLI-LOG-001
def test_console_logs_debug_when_verbose(capsys: pytest.CaptureFixture[str]) -> None:
    ulog.configure_console(verbose=True)
    log.debug("debug detail")
    assert "debug detail" in capsys.readouterr().err


# @spec CLI-LOG-001
def test_configuring_twice_does_not_duplicate_lines(capsys: pytest.CaptureFixture[str]) -> None:
    ulog.configure_console(verbose=False)
    ulog.configure_console(verbose=False)
    log.info("once")
    assert capsys.readouterr().err.count("once") == 1


# @spec CLI-LOG-002
def test_run_log_writes_json_lines_with_required_fields(tmp_path: Path) -> None:
    with ulog.run_log(tmp_path, "run-0001", "ingest") as path:
        log.info("file hashed", extra={"event": "unit_done", "unit_id": "u1", "bytes": 1024})
        log.warning("no unit here", extra={"event": "batch_written"})
    assert path == tmp_path / "logs" / "run-0001.log"
    first, second = read_lines(path)
    assert first["level"] == "INFO"
    assert first["run_id"] == "run-0001"
    assert first["stage"] == "ingest"
    assert first["unit_id"] == "u1"
    assert first["event"] == "unit_done"
    assert first["message"] == "file hashed"
    assert first["bytes"] == 1024
    assert datetime.fromisoformat(first["ts_utc"]).utcoffset() == timedelta(0)
    assert second["level"] == "WARNING"
    assert second["unit_id"] is None


# @spec CLI-LOG-002
def test_run_log_stops_writing_when_closed(tmp_path: Path) -> None:
    with ulog.run_log(tmp_path, "run-0002", "screen") as path:
        log.info("inside")
    log.info("outside")
    assert [line["message"] for line in read_lines(path)] == ["inside"]


# @spec CLI-LOG-005
def test_private_values_are_redacted_everywhere(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    secrets = {
        "URBANBIO_BUCKET": "bucket-secret-7f3a",
        "URBANBIO_SITES": SITES_TOML,
        "AWS_ACCESS_KEY_ID": "AKIATESTKEYID000",
        "AWS_SECRET_ACCESS_KEY": "aws-secret-value-123",
        "AWS_SESSION_TOKEN": "session-token-456",
    }
    for var, value in secrets.items():
        monkeypatch.setenv(var, value)
    ulog.configure_console(verbose=False)
    with ulog.run_log(tmp_path, "run-0003", "archive") as path:
        log.info("opening %s", secrets["URBANBIO_BUCKET"])
        log.info("key %s secret %s", secrets["AWS_ACCESS_KEY_ID"], secrets["AWS_SECRET_ACCESS_KEY"])
        log.info("token " + secrets["AWS_SESSION_TOKEN"])
        log.info("sites were %s", secrets["URBANBIO_SITES"])
    outputs = {"console": capsys.readouterr().err, "file": path.read_text()}
    for where, text in outputs.items():
        assert "opening" in text, where
        for value in secrets.values():
            assert value not in text, where


# @spec CLI-LOG-005
def test_each_exact_coordinate_is_redacted_on_its_own(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    # Written with a trailing zero, so the text in the variable and Python's formatting of the
    # parsed float differ: "12.34567890" and "12.3456789".
    sites = SITES_TOML.replace("12.3456789", "12.34567890")
    monkeypatch.setenv("URBANBIO_SITES", sites)
    ulog.configure_console(verbose=False)
    with ulog.run_log(tmp_path, "run-0004", "ingest") as path:
        log.info("header position %s, %s", "12.34567890", "-45.6789123")
        log.info("parsed position %s, %s", 12.3456789, -45.6789123)
        log.info("offset_m=%s", 3.2)
    outputs = {"console": capsys.readouterr().err, "file": path.read_text()}
    for where, text in outputs.items():
        assert "header position" in text, where
        assert "offset_m=3.2" in text, where
        for digits in EXACT_DIGITS:
            assert digits not in text, where


# @spec CLI-LOG-005
def test_empty_aws_variable_redacts_nothing(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv("AWS_PAGER", "")
    ulog.configure_console(verbose=False)
    log.info("retrieval registered")
    assert "retrieval registered" in capsys.readouterr().err


# --- Local paths ------------------------------------------------------------------------


# @spec CLI-LOG-007
def test_local_paths_are_logged_relative_to_their_configured_path(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    home = tmp_path / "home" / "operatorname"
    roots = {
        "quarantine": home / "urbanbio" / "quarantine",
        "work": home / "urbanbio" / "work",
        "state": home / "urbanbio" / "state",
        "model_cache": home / ".cache" / "urbanbio" / "birdnet",
        "test_audio_cache": home / ".cache" / "urbanbio" / "test-audio",
    }
    ulog.set_path_roots(roots)
    ulog.configure_console(verbose=False)
    with ulog.run_log(roots["state"], "run-0005", "archive") as path:
        log.info("read %s", roots["quarantine"] / "r1" / "Data" / "f1.wav")
        log.info("staged %s", roots["work"] / "r1" / "f1.flac")
        log.info("model in %s", roots["model_cache"])
        log.info("card at %s", tmp_path / "media" / "operatorname" / "SD" / "Data")
    outputs = {"console": capsys.readouterr().err, "file": path.read_text()}
    for where, text in outputs.items():
        assert "read quarantine:r1/Data/f1.wav" in text, where
        assert "staged work:r1/f1.flac" in text, where
        assert "model in model_cache:" in text, where
        assert "card at Data" in text, where
        assert "operatorname" not in text, where
        assert str(tmp_path) not in text, where


# @spec CLI-LOG-007
def test_nested_configured_paths_use_the_closest_one(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    # A configured path inside another one: the deeper name wins.
    ulog.set_path_roots({"state": tmp_path / "s", "work": tmp_path / "s" / "work"})
    ulog.configure_console(verbose=False)
    log.info("wrote %s", tmp_path / "s" / "work" / "x.flac")
    assert "wrote work:x.flac" in capsys.readouterr().err
