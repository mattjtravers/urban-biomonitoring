"""The `[store]` table (store LLD § Configuration)."""

from __future__ import annotations

import pytest

from tests.conftest import Project
from urbanbio import config
from urbanbio.errors import ConfigError


# @spec STORE-CFG-001
@pytest.mark.parametrize("value", ["gcs", "S3", "local", ""])
def test_only_s3_and_filesystem_are_backends(dev: Project, value: str) -> None:
    dev.operator_config(f'[store]\nbackend = "{value}"\n')
    with pytest.raises(ConfigError):
        config.load(with_sites=False)


# @spec STORE-CFG-002
@pytest.mark.parametrize("line", ['region = "us-east-1"', "glacier_transition_days = 30"])
def test_s3_keys_are_refused_with_the_filesystem_backend(dev: Project, line: str) -> None:
    dev.operator_config(f"[store]\n{line}\n")
    with pytest.raises(ConfigError):
        config.load(with_sites=False)


# @spec STORE-CFG-002
def test_root_is_refused_with_the_s3_backend(processing: Project) -> None:
    processing.operator_config(f'[store]\nroot = "{processing.home / "share"}"\n')
    with pytest.raises(ConfigError):
        config.load(with_sites=False)


# @spec STORE-CFG-004
def test_filesystem_backend_requires_a_root(dev: Project) -> None:
    dev.write(
        "config/dev.toml",
        (dev.root / "config" / "dev.toml").read_text().replace('root = "~/urbanbio-dev/store"', ""),
    )
    with pytest.raises(ConfigError):
        config.load(with_sites=False)


# @spec STORE-CFG-004
def test_filesystem_root_is_expanded_and_resolved(dev: Project) -> None:
    dev.operator_config('[store]\nroot = "~/shares/../lab-share/urbanbio"\n')
    cfg = config.load(with_sites=False)
    assert cfg.store.root == dev.home.resolve() / "lab-share" / "urbanbio"


# @spec STORE-CFG-004
def test_filesystem_root_inside_the_working_tree_is_refused(dev: Project) -> None:
    dev.operator_config(f'[store]\nroot = "{dev.root / "store"}"\n')
    with pytest.raises(ConfigError):
        config.load(with_sites=False)
