"""Configuration loading: profiles, layers, the operator file, paths, and the dev profile."""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from tests.conftest import SITES_TOML, Project
from urbanbio import config, errors

# --- Profiles ---------------------------------------------------------------------------


# @spec CLI-PROF-001
@pytest.mark.parametrize("value", [None, "", "production", "Dev", "review"])
def test_load_refuses_unset_or_unknown_profile(project: Project, value: str | None) -> None:
    if value is not None:
        project.profile(value)
    with pytest.raises(errors.ConfigError):
        config.load(with_sites=False)


# @spec CLI-PROF-002
def test_profile_is_never_inferred_from_the_host(project: Project) -> None:
    # Variables a hosted development environment or CI sets must not stand in for a profile.
    for var, value in {"CODESPACES": "true", "CI": "true", "GITHUB_ACTIONS": "true"}.items():
        project.monkeypatch.setenv(var, value)
    with pytest.raises(errors.ConfigError):
        config.load(with_sites=False)


# @spec CLI-PROF-002
@pytest.mark.parametrize("profile", ["processing", "dev"])
def test_profile_comes_from_the_variable(processing: Project, profile: str) -> None:
    processing.profile(profile)
    if profile == "dev":
        processing.monkeypatch.delenv("URBANBIO_SITES")
    assert config.load(with_sites=False).profile == profile


# --- Layers -----------------------------------------------------------------------------


# @spec CLI-CFG-001
def test_layers_combine_pipeline_and_profile(processing: Project) -> None:
    cfg = config.load(with_sites=False)
    assert cfg.screen.padding_s == 2.0
    assert cfg.detect.confidence_floor == 0.10
    assert cfg.publish.coordinate_precision_deg == 0.01
    assert cfg.resources.workers == 2
    assert cfg.quarantine.budget_gb == 35.0
    assert cfg.store.backend == "s3"


# @spec CLI-CFG-001
def test_operator_file_replaces_only_the_keys_it_sets(processing: Project) -> None:
    processing.operator_config("[resources]\nworkers = 4\n\n[quarantine]\nbudget_gb = 120.0\n")
    cfg = config.load(with_sites=False)
    assert cfg.resources.workers == 4
    assert cfg.quarantine.budget_gb == 120.0
    # Keys the operator file doesn't set keep the profile's values, even in a table it touches.
    assert cfg.quarantine.min_free_gb == 3.0
    assert cfg.store.backend == "s3"
    assert cfg.paths.quarantine == (processing.home / "urbanbio" / "quarantine").resolve()


# @spec CLI-CFG-001
def test_operator_file_can_switch_store_backend(processing: Project, tmp_path: Path) -> None:
    share = tmp_path / "share"
    processing.operator_config(f'[store]\nbackend = "filesystem"\nroot = "{share}"\n')
    cfg = config.load(with_sites=False)
    assert cfg.store.backend == "filesystem"
    assert cfg.store.root == share.resolve()


# @spec CLI-CFG-001
def test_an_operator_backend_switch_replaces_the_store_table(
    processing: Project, tmp_path: Path
) -> None:
    # processing.toml sets the s3-only `region` and `glacier_transition_days`; they don't carry
    # over to the operator's filesystem store.
    share = tmp_path / "share"
    processing.operator_config(f'[store]\nbackend = "filesystem"\nroot = "{share}"\n')
    cfg = config.load(with_sites=False)
    assert cfg.store.region is None
    assert cfg.store.glacier_transition_days is None


# @spec CLI-CFG-001
def test_an_operator_store_table_without_backend_merges_per_key(
    dev: Project, tmp_path: Path
) -> None:
    share = tmp_path / "share"
    dev.operator_config(f'[store]\nroot = "{share}"\n')
    cfg = config.load(with_sites=False)
    assert (cfg.store.backend, cfg.store.root) == ("filesystem", share.resolve())


# @spec CLI-CFG-001
def test_stage_parameters_are_identical_in_every_profile(processing: Project) -> None:
    proc = config.load(with_sites=False)
    processing.profile("dev")
    processing.monkeypatch.delenv("URBANBIO_SITES")
    dev = config.load(with_sites=False)
    for table in ("recording", "screen", "detect", "archive", "publish"):
        assert getattr(proc, table) == getattr(dev, table)


# @spec CLI-CFG-002
@pytest.mark.parametrize("table", ["paths", "resources", "quarantine", "store"])
def test_pipeline_file_may_not_hold_deployment_tables(processing: Project, table: str) -> None:
    pipeline = processing.root / "config" / "pipeline.toml"
    pipeline.write_text(pipeline.read_text() + f"\n[{table}]\n")
    with pytest.raises(errors.ConfigError):
        config.load(with_sites=False)


# @spec CLI-CFG-002
def test_profile_file_may_hold_only_deployment_tables(processing: Project) -> None:
    profile = processing.root / "config" / "processing.toml"
    profile.write_text(profile.read_text() + "\n[screen]\npadding_s = 3.0\n")
    with pytest.raises(errors.ConfigError):
        config.load(with_sites=False)


# @spec CLI-CFG-002
@pytest.mark.parametrize("text", ["[detect]\nconfidence_floor = 0.5\n", "[publish]\n"])
def test_operator_file_may_hold_only_deployment_tables(processing: Project, text: str) -> None:
    processing.operator_config(text)
    with pytest.raises(errors.ConfigError):
        config.load(with_sites=False)


# @spec CLI-CFG-003
def test_operator_file_path_must_be_absolute(processing: Project) -> None:
    processing.operator_config("[resources]\nworkers = 4\n")
    processing.monkeypatch.chdir(processing.home)
    processing.monkeypatch.setenv("URBANBIO_CONFIG", ".config/urbanbio/config.toml")
    with pytest.raises(errors.ConfigError):
        config.load(with_sites=False)


# @spec CLI-CFG-003
def test_operator_file_path_may_use_home(processing: Project) -> None:
    processing.operator_config("[resources]\nworkers = 4\n")
    processing.monkeypatch.setenv("URBANBIO_CONFIG", "~/.config/urbanbio/config.toml")
    assert config.load(with_sites=False).resources.workers == 4


# @spec CLI-CFG-003
def test_operator_file_must_exist(processing: Project) -> None:
    processing.monkeypatch.setenv("URBANBIO_CONFIG", str(processing.home / "missing.toml"))
    with pytest.raises(errors.ConfigError):
        config.load(with_sites=False)


# @spec CLI-CFG-003
def test_operator_file_must_be_a_regular_file(processing: Project) -> None:
    directory = processing.home / "conf.d"
    directory.mkdir()
    processing.monkeypatch.setenv("URBANBIO_CONFIG", str(directory))
    with pytest.raises(errors.ConfigError):
        config.load(with_sites=False)


# @spec CLI-CFG-003
def test_operator_file_must_lie_outside_the_working_tree(processing: Project) -> None:
    inside = processing.write("operator.toml", "[resources]\nworkers = 4\n")
    processing.monkeypatch.setenv("URBANBIO_CONFIG", str(inside))
    with pytest.raises(errors.ConfigError):
        config.load(with_sites=False)


# @spec CLI-CFG-004
@pytest.mark.parametrize(
    ("relpath", "old", "new"),
    [
        ("config/processing.toml", "quarantine = ", "quarantin = "),  # misspelled key
        ("config/pipeline.toml", "padding_s = 2.0", "padding_s = 2.0\npading_s = 3.0"),
        ("config/processing.toml", "workers = 2", 'workers = "two"'),  # wrong type
        ("config/pipeline.toml", "sample_rate_hz = 48000", 'sample_rate_hz = "48k"'),
    ],
)
def test_unknown_keys_and_wrong_types_are_refused(
    processing: Project, relpath: str, old: str, new: str
) -> None:
    path = processing.root / relpath
    path.write_text(path.read_text().replace(old, new, 1))
    with pytest.raises(errors.ConfigError):
        config.load(with_sites=False)


# @spec CLI-CFG-004
def test_unknown_key_in_operator_file_is_refused(processing: Project) -> None:
    processing.operator_config("[resources]\nworker = 4\n")
    with pytest.raises(errors.ConfigError):
        config.load(with_sites=False)


# --- Paths ------------------------------------------------------------------------------


# @spec CLI-CFG-005
def test_paths_are_expanded_and_resolved(processing: Project) -> None:
    cfg = config.load(with_sites=False)
    home = processing.home.resolve()
    assert cfg.paths.quarantine == home / "urbanbio" / "quarantine"
    assert cfg.paths.work == home / "urbanbio" / "work"
    assert cfg.paths.state == home / "urbanbio" / "state"
    assert cfg.paths.model_cache == home / ".cache" / "urbanbio" / "birdnet"
    assert cfg.paths.test_audio_cache == home / ".cache" / "urbanbio" / "test-audio"


# @spec CLI-CFG-005
def test_dev_store_root_is_expanded_and_resolved(dev: Project) -> None:
    cfg = config.load(with_sites=False)
    assert cfg.store.root == dev.home.resolve() / "urbanbio-dev" / "store"


# @spec CLI-CFG-005
def test_path_inside_the_working_tree_is_refused(processing: Project) -> None:
    processing.operator_config(f'[paths]\nwork = "{processing.root / "work"}"\n')
    with pytest.raises(errors.ConfigError):
        config.load(with_sites=False)


# @spec CLI-CFG-005
def test_symlink_into_the_working_tree_is_refused(processing: Project) -> None:
    target = processing.root / "data" / "quarantine"
    target.mkdir(parents=True)
    link = processing.home / "q"
    link.symlink_to(target)
    processing.operator_config(f'[paths]\nquarantine = "{link}"\n')
    with pytest.raises(errors.ConfigError):
        config.load(with_sites=False)


# @spec CLI-CFG-005
def test_store_root_inside_the_working_tree_is_refused(dev: Project) -> None:
    dev.operator_config(f'[store]\nroot = "{dev.root / "store"}"\n')
    with pytest.raises(errors.ConfigError):
        config.load(with_sites=False)


# --- Dev profile ------------------------------------------------------------------------


# @spec CLI-DEV-001
def test_dev_sites_come_from_the_committed_synthetic_file(dev: Project) -> None:
    cfg = config.load(with_sites=True)
    assert [s.site_id for s in cfg.sites] == ["site7"]


# @spec CLI-DEV-002
@pytest.mark.parametrize("with_sites", [True, False])
def test_dev_refuses_private_sites(dev: Project, with_sites: bool) -> None:
    dev.monkeypatch.setenv("URBANBIO_SITES", SITES_TOML)
    with pytest.raises(errors.ConfigError) as excinfo:
        config.load(with_sites=with_sites)
    assert excinfo.value.reason_code == "dev_private_sites"


# --- The committed configuration --------------------------------------------------------


# @spec CLI-CFG-001, CLI-CFG-004, CLI-DEV-001
@pytest.mark.parametrize("profile", ["processing", "dev"])
def test_committed_configuration_loads(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, profile: str
) -> None:
    """The real config/ files load cleanly with the real repository root."""
    for var in [v for v in os.environ if v.startswith(("URBANBIO_", "AWS_"))]:
        monkeypatch.delenv(var)
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("URBANBIO_PROFILE", profile)
    if profile == "processing":
        monkeypatch.setenv("URBANBIO_SITES", SITES_TOML)
    cfg = config.load(with_sites=True)
    assert config.repo_root() == Path(__file__).resolve().parents[2]
    assert cfg.profile == profile
    assert cfg.sites
