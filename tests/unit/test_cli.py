"""The `urbanbio` command line: registration, profile handling, and the config commands."""

from __future__ import annotations

import tomllib
from pathlib import Path

import pytest
import typer
from typer.core import TyperGroup
from typer.testing import CliRunner, Result

from tests.conftest import EXACT_DIGITS, SITES_TOML, Project
from urbanbio import cli

REPO = Path(__file__).resolve().parents[2]

# config-cli § CLI: every command, as a path of command names.
COMMANDS = {
    ("config", "check"),
    ("config", "paths"),
    ("store", "init"),
    ("site", "list"),
    ("deployment", "create"),
    ("deployment", "end"),
    ("ingest",),
    ("screen",),
    ("archive",),
    ("detect",),
    ("purge",),
    ("process",),
    ("retrieval", "status"),
    ("speech", "sample"),
    ("speech", "label"),
    ("speech", "recall"),
    ("rescreen",),
    ("runs", "list"),
    ("runs", "show"),
    ("runs", "unflag"),
    ("query",),
}

# Commands refused in dev, with arguments that parse, so a refusal can't be a usage error.
DEV_REFUSED = {
    "ingest": ["ingest", "{card}", "--deployment", "d1", "--retrieved", "2026-01-01T00:00:00Z"],
    "process": ["process", "{card}", "--deployment", "d1", "--retrieved", "2026-01-01T00:00:00Z"],
    "screen": ["screen", "--retrieval", "r1"],
    "speech sample": ["speech", "sample", "--retrieval", "r1", "--n", "5"],
    "speech label --import": ["speech", "label", "--import"],
    "archive": ["archive", "--retrieval", "r1"],
    "purge": ["purge", "--retrieval", "r1"],
    "rescreen": ["rescreen", "--retrieval", "r1"],
}


def run(*args: str) -> Result:
    return CliRunner().invoke(cli.app, list(args))


def command_paths(group: TyperGroup, prefix: tuple[str, ...] = ()) -> set[tuple[str, ...]]:
    paths: set[tuple[str, ...]] = set()
    for name, command in group.commands.items():
        if isinstance(command, TyperGroup):
            paths |= command_paths(command, (*prefix, name))
        else:
            paths.add((*prefix, name))
    return paths


def snapshot(root: Path) -> set[Path]:
    return set(root.rglob("*"))


# --- Registration -----------------------------------------------------------------------


# @spec CLI-CMD-001
def test_console_script_is_installed() -> None:
    scripts = tomllib.loads((REPO / "pyproject.toml").read_text())["project"]["scripts"]
    assert scripts["urbanbio"].startswith("urbanbio.cli:")


# @spec CLI-CMD-001
def test_every_command_is_registered() -> None:
    group = typer.main.get_command(cli.app)
    assert isinstance(group, TyperGroup)
    assert command_paths(group) == COMMANDS


# --- Profile ----------------------------------------------------------------------------


# @spec CLI-PROF-001, CLI-EXIT-001
@pytest.mark.parametrize("profile", [None, "staging"])
@pytest.mark.parametrize("args", [["config", "paths"], ["config", "check"], ["site", "list"]])
def test_command_without_a_valid_profile_exits_3(
    project: Project, profile: str | None, args: list[str]
) -> None:
    if profile is not None:
        project.profile(profile)
    assert run(*args).exit_code == 3


# @spec CLI-PROF-001
@pytest.mark.parametrize("args", [["--help"], ["config", "--help"], ["site", "list", "--help"]])
def test_help_works_without_a_profile(project: Project, args: list[str]) -> None:
    result = run(*args)
    assert result.exit_code == 0
    assert "Usage" in result.output


# --- Dev profile ------------------------------------------------------------------------


# @spec CLI-DEV-002
@pytest.mark.parametrize("args", [["config", "paths"], ["site", "list"], ["config", "check"]])
def test_dev_with_private_sites_exits_3(dev: Project, args: list[str]) -> None:
    dev.monkeypatch.setenv("URBANBIO_SITES", SITES_TOML)
    result = run(*args)
    assert result.exit_code == 3
    assert "dev_private_sites" in result.output
    for digits in EXACT_DIGITS:
        assert digits not in result.output


# @spec CLI-DEV-003
@pytest.mark.parametrize("name", sorted(DEV_REFUSED))
def test_dev_refuses_real_media_and_archive_commands(
    dev: Project, tmp_path: Path, name: str
) -> None:
    card = tmp_path / "card"
    (card / "Data").mkdir(parents=True)
    (card / "Data" / "placeholder.txt").write_text("not audio")
    before = snapshot(tmp_path)
    args = [a.format(card=card) for a in DEV_REFUSED[name]]
    result = run(*args)
    assert result.exit_code == 3, result.output
    # Before doing anything: no file created, moved, or deleted anywhere under the test root.
    assert snapshot(tmp_path) == before


# --- config paths -----------------------------------------------------------------------


# @spec CLI-CMD-003
def test_config_paths_prints_expanded_resolved_paths(processing: Project) -> None:
    result = run("config", "paths")
    assert result.exit_code == 0, result.output
    home = processing.home.resolve()
    for name, path in {
        "quarantine": home / "urbanbio" / "quarantine",
        "work": home / "urbanbio" / "work",
        "state": home / "urbanbio" / "state",
        "model_cache": home / ".cache" / "urbanbio" / "birdnet",
        "test_audio_cache": home / ".cache" / "urbanbio" / "test-audio",
    }.items():
        assert name in result.output
        assert str(path) in result.output
    assert "~" not in result.output


# @spec CLI-CMD-003, CLI-PRIV-007
def test_config_paths_reads_no_private_value_and_no_store(processing: Project) -> None:
    # Processing with the s3 backend, and no sites, bucket, or AWS keys in the environment.
    processing.monkeypatch.delenv("URBANBIO_SITES")
    before = snapshot(processing.home)
    result = run("config", "paths")
    assert result.exit_code == 0, result.output
    assert snapshot(processing.home) == before


# --- site list --------------------------------------------------------------------------


# @spec CLI-CMD-004
def test_site_list_prints_generalized_coordinates_only(processing: Project) -> None:
    result = run("site", "list")
    assert result.exit_code == 0, result.output
    assert "site1" in result.output
    assert "12.35" in result.output
    assert "-45.68" in result.output
    for digits in EXACT_DIGITS:
        assert digits not in result.output


# @spec CLI-CMD-004, CLI-DEV-001
def test_site_list_in_dev_shows_synthetic_sites(dev: Project) -> None:
    result = run("site", "list")
    assert result.exit_code == 0, result.output
    assert "site7" in result.output


# @spec CLI-PRIV-007, CLI-EXIT-001
def test_site_list_without_sites_in_processing_exits_3(processing: Project) -> None:
    processing.monkeypatch.delenv("URBANBIO_SITES")
    assert run("site", "list").exit_code == 3
