"""Run records, batches, and write order (runs LLD § Run Record, § Write Order, § Current Rows).

Runs use the dev profile's `filesystem` store. Tests that don't concern git state pin the code
state with `clean_code`; the git tests build a real repository in the test's working tree.
"""

from __future__ import annotations

import json
import re
import subprocess
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest

from tests.conftest import Project, store_init
from tests.unit.test_models_records import deployment
from tests.unit.test_tables import WAIVER_UNIT, waiver
from urbanbio import config
from urbanbio.errors import PrivacyError
from urbanbio.provenance import runs, versions
from urbanbio.storage import tables
from urbanbio.storage.store import open_store

PARAMS = {"padding_s": 2.0, "threshold": 0.1}
RUN_ID_RE = re.compile(r"^screen-\d{8}T\d{6}Z-[0-9a-f]{4}$")


@pytest.fixture
def env(fs_env: Project) -> Project:
    assert store_init().exit_code == 0
    return fs_env


@pytest.fixture
def clean_code(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        versions,
        "code_state",
        lambda root: versions.CodeState(
            package_version="0.0.0", git_commit="a" * 40, git_dirty=False
        ),
    )


def start(stage: str = "screen", **kwargs: Any) -> runs.Run:
    cfg = config.load(with_sites=True)
    store = open_store(cfg)
    values = {"stage_version": 1, "params": PARAMS, "scope": {"retrieval_id": "site7-dep001-r001"}}
    return runs.start_run(store, cfg, stage=stage, **{**values, **kwargs})


def record(run: runs.Run, tmp_path: Path) -> dict[str, Any]:
    dest = tmp_path / f"{run.run_id}.json"
    run.store.get(f"runs/{run.run_id}.json", dest)
    return json.loads(dest.read_text())


def query(run: runs.Run, sql: str) -> list[tuple]:
    return tables.connect(run.store).execute(sql).fetchall()


# --- Run IDs and the record -----------------------------------------------------------------


# @spec RUN-REC-001
def test_run_ids() -> None:
    now = datetime(2026, 10, 12, 14, 5, 0, tzinfo=UTC)
    run_id = runs.new_run_id("screen", now)
    assert RUN_ID_RE.match(run_id)
    assert run_id.startswith("screen-20261012T140500Z-")


# @spec RUN-REC-001
def test_start_writes_a_running_record(env: Project, clean_code: None, tmp_path: Path) -> None:
    run = start()
    assert RUN_ID_RE.match(run.run_id)
    data = record(run, tmp_path)
    assert data["status"] == "running"
    assert data["run_id"] == run.run_id
    assert data["ended_utc"] is None


# @spec RUN-REC-006
@pytest.mark.parametrize(
    "params",
    [
        {"latitude_exact": 1.0},
        {"nested": {"longitude_exact": 1.0}},
        {"sites": [{"address": "x"}]},
    ],
)
def test_private_parameters_are_refused_before_the_record(
    env: Project, clean_code: None, params: dict[str, Any]
) -> None:
    with pytest.raises(PrivacyError):
        start(params=params)
    store = open_store(config.load(with_sites=False))
    assert list(store.list("runs/")) == []


# @spec RUN-REC-002, RUN-REC-004
def test_finish_rewrites_the_record_with_every_field(
    env: Project, clean_code: None, tmp_path: Path
) -> None:
    run = start()
    run.skip("af-0123456789abcdef")
    run.finish()
    data = record(run, tmp_path)
    assert set(data) >= {
        "run_id",
        "stage",
        "status",
        "started_utc",
        "ended_utc",
        "code",
        "store",
        "environment",
        "models",
        "params",
        "params_hash",
        "scope",
        "counts",
        "resources",
        "errors",
        "outputs",
        "log_key",
    }
    assert data["status"] == "succeeded"
    assert data["code"] == {
        "package_version": "0.0.0",
        "git_commit": "a" * 40,
        "git_dirty": False,
        "stage_version": 1,
    }
    assert data["store"]["backend"] == "filesystem"
    assert re.fullmatch(r"st-[0-9a-f]{16}", data["store"]["store_id"])
    assert set(data["store"]) == {"backend", "store_id"}
    assert data["environment"]["URBANBIO_PROFILE"] == "dev"
    for name in (
        "python",
        "platform",
        "birdnet",
        "silero-vad",
        "torch",
        "soundfile",
        "libsndfile",
        "pyarrow",
        "duckdb",
        "boto3",
    ):
        assert name in data["environment"], name
    assert set(data["resources"]) >= {
        "workers",
        "cpu_count",
        "mem_total_mb",
        "peak_rss_mb",
        "wall_clock_s",
    }
    assert data["params"] == PARAMS
    assert data["log_key"] == f"runs/{run.run_id}.log"
    assert data["counts"]["skipped"] == 1


# @spec RUN-REC-004
def test_the_record_never_names_the_store_root(
    env: Project, clean_code: None, tmp_path: Path
) -> None:
    run = start()
    run.finish()
    assert str(env.home) not in json.dumps(record(run, tmp_path))


# @spec RUN-REC-002
def test_finish_writes_a_counted_runs_row(env: Project, clean_code: None) -> None:
    run = start()
    run.finish()
    assert query(run, "SELECT run_id, status, git_dirty FROM runs") == [
        (run.run_id, "succeeded", False)
    ]


# @spec RUN-REC-003
@pytest.mark.parametrize(
    ("statuses", "expected"),
    [
        ([], "succeeded"),
        (["succeeded", "succeeded"], "succeeded"),
        (["succeeded", "flagged"], "partial"),
        (["failed"], "partial"),
    ],
)
def test_final_status(
    env: Project, clean_code: None, tmp_path: Path, statuses: list[str], expected: str
) -> None:
    run = start()
    rows = [
        run.unit_row(
            f"af-{i:016x}",
            "k" * 64,
            status,
            reason_code=None if status == "succeeded" else "truncated",
        )
        for i, status in enumerate(statuses)
    ]
    if rows:
        run.write_batch({}, rows)
    run.skip("af-ffffffffffffffff")
    run.finish()
    assert record(run, tmp_path)["status"] == expected


# @spec RUN-REC-003
def test_a_run_level_error_ends_the_run_failed(
    env: Project, clean_code: None, tmp_path: Path
) -> None:
    run = start()
    run.finish(failed=True)
    assert record(run, tmp_path)["status"] == "failed"


# @spec RUN-REC-004
def test_counts_are_kept_per_ledger_stage(env: Project, clean_code: None, tmp_path: Path) -> None:
    run = start("archive")
    run.write_batch(
        {},
        [
            run.unit_row("af-0000000000000001:v1", "k" * 64, "succeeded"),
            run.unit_row("af-0000000000000001", "p" * 64, "succeeded", stage="purge"),
        ],
    )
    run.finish()
    counts = record(run, tmp_path)["counts"]
    assert counts["succeeded"] == 2
    assert counts["by_stage"]["archive"]["succeeded"] == 1
    assert counts["by_stage"]["purge"]["succeeded"] == 1


# @spec RUN-REC-007
def test_errors_hold_only_unit_reason_and_message(
    env: Project, clean_code: None, tmp_path: Path
) -> None:
    run = start()
    run.error("af-0123456789abcdef", "decode_error", "libsndfile couldn't decode the file")
    run.finish()
    (error,) = record(run, tmp_path)["errors"]
    assert error == {
        "unit_id": "af-0123456789abcdef",
        "reason_code": "decode_error",
        "message": "libsndfile couldn't decode the file",
    }


# --- Batches and write order ----------------------------------------------------------------


# @spec RUN-ORDER-001
def test_batches_hold_at_most_fifty_units() -> None:
    assert runs.RUN_BATCH_UNITS == 50
    assert [len(b) for b in runs.batches(range(120))] == [50, 50, 20]
    assert list(runs.batches([])) == []


def spy_puts(run: runs.Run, monkeypatch: pytest.MonkeyPatch) -> list[str]:
    events: list[str] = []
    real = run.store.put

    def put(key: str, *args: Any, **kwargs: Any) -> Any:
        result = real(key, *args, **kwargs)
        events.append(key)
        return result

    monkeypatch.setattr(run.store, "put", put)
    return events


# @spec RUN-ORDER-002, RUN-ORDER-004, DATA-PQ-005
def test_a_batch_writes_tables_then_ledger_then_cleans_up(
    env: Project, clean_code: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    run = start("admin")
    events = spy_puts(run, monkeypatch)
    row = tables.TableRow(waiver(run.run_id), stage="admin", unit_id=WAIVER_UNIT)
    run.write_batch(
        {"waivers": [row]},
        [run.unit_row(WAIVER_UNIT, "k" * 64, "succeeded")],
        cleanup=lambda: events.append("cleanup"),
    )
    table_key = f"curated/waivers/{run.run_id}.parquet"
    ledger_key = f"curated/stage_units/stage=admin/{run.run_id}-0001.parquet"
    log_key = f"runs/{run.run_id}.log"
    assert table_key in events and ledger_key in events and log_key in events
    assert events.index(table_key) < events.index(ledger_key) < events.index("cleanup")
    assert events.index(ledger_key) < events.index(log_key)


# @spec RUN-ORDER-002, DATA-PQ-005
def test_part_files_are_named_by_run_and_batch(
    env: Project, clean_code: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    run = start("admin")
    events = spy_puts(run, monkeypatch)
    for unit in ("site1-dep001", "site1-dep002"):
        row = tables.TableRow(
            deployment(deployment_id=unit), stage="admin", unit_id=unit, run_id=run.run_id
        )
        run.write_batch({"deployments": [row]}, [run.unit_row(unit, "k" * 64, "succeeded")])
    assert f"curated/deployments/site1-dep001-{run.run_id}.parquet" in events
    assert f"curated/deployments/site1-dep002-{run.run_id}.parquet" in events
    assert f"curated/stage_units/stage=admin/{run.run_id}-0001.parquet" in events
    assert f"curated/stage_units/stage=admin/{run.run_id}-0002.parquet" in events


# @spec RUN-ORDER-004
def test_cleanup_waits_for_the_ledger(
    env: Project, clean_code: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    run = start()
    real = run.store.put

    def put(key: str, *args: Any, **kwargs: Any) -> Any:
        if key.startswith("curated/stage_units/"):
            raise OSError("ledger write failed")
        return real(key, *args, **kwargs)

    monkeypatch.setattr(run.store, "put", put)
    cleaned: list[str] = []
    with pytest.raises(OSError):
        run.write_batch(
            {},
            [run.unit_row("af-0123456789abcdef", "k" * 64, "succeeded")],
            cleanup=lambda: cleaned.append("x"),
        )
    assert cleaned == []


# @spec RUN-DIRTY-001, RUN-LEDGER-001
def test_ledger_rows_copy_the_run(env: Project, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        versions,
        "code_state",
        lambda root: versions.CodeState(
            package_version="0.0.0", git_commit="a" * 40, git_dirty=True
        ),
    )
    run = start()
    row = run.unit_row("af-0123456789abcdef", "k" * 64, "succeeded", output_refs=["x"])
    assert row.run_id == run.run_id
    assert row.stage == "screen"
    assert row.git_dirty is True
    assert row.output_refs == ["x"]
    assert row.finished_utc.tzinfo is not None


# --- Run-level tables -----------------------------------------------------------------------


# @spec RUN-CURRENT-005
def test_run_level_tables_are_written_only_at_run_end(env: Project, clean_code: None) -> None:
    run = start()
    with pytest.raises(ValueError):
        run.write_batch({"sites": []}, [])


# @spec DATA-PQ-007
def test_a_sites_file_is_written_when_the_sites_change(env: Project, clean_code: None) -> None:
    first = start()
    first.finish()
    assert query(first, "SELECT site_id FROM current_sites") == [("site7",)]
    files = list(first.store.list("curated/sites/"))
    assert files == [f"curated/sites/{first.run_id}.parquet"]

    second = start()
    second.finish()
    assert list(second.store.list("curated/sites/")) == files

    sites = env.root / "config" / "dev-sites.toml"
    sites.write_text(sites.read_text().replace('"synthetic"', '"synthetic meadow"'))
    third = start()
    third.finish()
    assert len(list(third.store.list("curated/sites/"))) == 2
    assert query(third, "SELECT habitat FROM current_sites") == [("synthetic meadow",)]


# --- Code state -----------------------------------------------------------------------------


def git(root: Path, *args: str) -> None:
    subprocess.run(
        ["git", "-c", "user.email=test@example.invalid", "-c", "user.name=test", *args],
        cwd=root,
        check=True,
        capture_output=True,
    )


# @spec RUN-DIRTY-006
def test_code_outside_a_git_tree_is_dirty_with_no_commit(project: Project) -> None:
    state = versions.code_state(project.root)
    assert state.git_commit is None
    assert state.git_dirty is True


# @spec RUN-DIRTY-001
def test_code_state_reads_the_commit_and_dirtiness(project: Project) -> None:
    git(project.root, "init", "-q")
    git(project.root, "add", "-A")
    git(project.root, "commit", "-q", "-m", "init")
    state = versions.code_state(project.root)
    assert re.fullmatch(r"[0-9a-f]{40}", state.git_commit or "")
    assert state.git_dirty is False
    (project.root / "config" / "pipeline.toml").write_text("# changed\n")
    assert versions.code_state(project.root).git_dirty is True


# @spec RUN-DIRTY-001
def test_an_untracked_file_makes_the_tree_dirty(project: Project) -> None:
    git(project.root, "init", "-q")
    git(project.root, "add", "-A")
    git(project.root, "commit", "-q", "-m", "init")
    (project.root / "new_module.py").write_text("x = 1\n")
    assert versions.code_state(project.root).git_dirty is True


# @spec RUN-DIRTY-001, RUN-DIRTY-006
def test_a_run_records_the_real_code_state(env: Project, tmp_path: Path) -> None:
    run = start()
    run.finish()
    code = record(run, tmp_path)["code"]
    assert code["git_commit"] is None
    assert code["git_dirty"] is True
