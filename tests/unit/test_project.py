"""Project-level requirements: dependency pins and the repository hygiene guard."""

from __future__ import annotations

import shutil
import subprocess
import tomllib
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
HYGIENE = REPO / "scripts" / "check-repo-hygiene.sh"

RUNTIME = (
    "birdnet",
    "silero-vad",
    "torch",
    "soundfile",
    "scipy",
    "numpy",
    "pydantic",
    "typer",
    "boto3",
    "pyarrow",
    "duckdb",
)


def requirement_name(spec: str) -> str:
    for sep in ("[", "=", "<", ">", "~", "!", ";", " "):
        spec = spec.split(sep, 1)[0]
    return spec.strip().lower()


# @spec CLI-DEP-001
def test_python_version_is_pinned() -> None:
    assert (REPO / ".python-version").read_text().strip() == "3.13"


# @spec CLI-DEP-001
def test_dependencies_are_declared_and_locked() -> None:
    pyproject = tomllib.loads((REPO / "pyproject.toml").read_text())
    deps = pyproject["project"]["dependencies"]
    assert {requirement_name(d) for d in deps} >= set(RUNTIME)
    assert "birdnet==1.1.1" in deps
    assert "silero-vad==6.2.3" in deps
    dev = pyproject["dependency-groups"]["dev"]
    assert {"pytest", "ruff", "moto"} <= {requirement_name(d) for d in dev}
    assert any(d.startswith("moto[s3]") for d in dev)
    assert (REPO / "uv.lock").is_file()


# @spec CLI-DEP-001
def test_torch_comes_from_the_cpu_index() -> None:
    uv = tomllib.loads((REPO / "pyproject.toml").read_text())["tool"]["uv"]
    index_name = uv["sources"]["torch"]["index"]
    (index,) = [i for i in uv["index"] if i["name"] == index_name]
    assert index["url"] == "https://download.pytorch.org/whl/cpu"


# --- Repository hygiene -----------------------------------------------------------------


@pytest.fixture
def scratch_repo(tmp_path: Path) -> Path:
    if shutil.which("git") is None:
        pytest.fail("git is required for the repository hygiene tests")
    repo = tmp_path / "scratch"
    (repo / "scripts").mkdir(parents=True)
    shutil.copy(HYGIENE, repo / "scripts" / HYGIENE.name)
    subprocess.run(["git", "init", "-q", str(repo)], check=True)
    (repo / "README.md").write_text("ok\n")
    return repo


def hygiene(repo: Path, *tracked: tuple[str, bytes]) -> subprocess.CompletedProcess[str]:
    for relpath, content in tracked:
        path = repo / relpath
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content)
    subprocess.run(["git", "-C", str(repo), "add", "-A", "-f"], check=True)
    return subprocess.run(
        ["bash", "scripts/check-repo-hygiene.sh"], cwd=repo, capture_output=True, text=True
    )


# @spec CLI-TEST-004
def test_hygiene_passes_a_clean_tree(scratch_repo: Path) -> None:
    result = hygiene(scratch_repo, ("src/app.py", b"print('hi')\n"))
    assert result.returncode == 0, result.stdout


# @spec CLI-TEST-004
@pytest.mark.parametrize(
    "relpath",
    ["clip.wav", "a/B.FLAC", "x.w4v", "y.mp3", "z.ogg", "q.opus", "r.m4a", "s.aif", "t.AIFF"],
)
def test_hygiene_rejects_audio_extensions(scratch_repo: Path, relpath: str) -> None:
    assert hygiene(scratch_repo, (relpath, b"\x00" * 16)).returncode == 1


# @spec CLI-TEST-004
@pytest.mark.parametrize(
    "magic",
    [b"RIFF\x24\x00\x00\x00WAVEfmt ", b"fLaC\x00\x00\x00\x22", b"OggS\x00\x02", b"ID3\x04\x00"],
    ids=["wave", "flac", "ogg", "id3"],
)
def test_hygiene_rejects_audio_magic_bytes(scratch_repo: Path, magic: bytes) -> None:
    assert hygiene(scratch_repo, ("fixtures/sample.bin", magic + b"\x00" * 16)).returncode == 1


# @spec CLI-TEST-004
@pytest.mark.parametrize(
    "relpath",
    [
        "config/private/sites.toml",
        "quarantine/x.txt",
        "data/x.txt",
        "work/x.txt",
        "output/x.txt",
        "outputs/x.txt",
        "config/sites.local.toml",
        "config/sites.local.yaml",
        "config/sites.local.yml",
        ".env",
        "deploy/.env",
        ".claude/settings.local.json",
    ],
)
def test_hygiene_rejects_private_and_local_data_paths(scratch_repo: Path, relpath: str) -> None:
    assert hygiene(scratch_repo, (relpath, b"x\n")).returncode == 1


# @spec CLI-TEST-004
@pytest.mark.parametrize("relpath", ["src/data/x.txt", "docs/work/x.md", "tests/output/x.txt"])
def test_hygiene_allows_nested_directories_with_local_data_names(
    scratch_repo: Path, relpath: str
) -> None:
    assert hygiene(scratch_repo, (relpath, b"x\n")).returncode == 0
