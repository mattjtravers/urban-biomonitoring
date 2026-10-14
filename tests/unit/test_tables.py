"""Parquet tables and DuckDB views (data-model LLD § Parquet Tables, § Querying with DuckDB;
runs LLD § Current Rows).

Every test uses a `filesystem` store in a temporary directory. The `s3` path of `connect` needs
DuckDB's `httpfs` extension, which is downloaded on first use, so it isn't exercised here.
"""

from __future__ import annotations

import io
from datetime import UTC, datetime, timedelta
from typing import Any

import pyarrow.parquet as pq
import pytest

from tests.unit.test_models_records import deployment, stage_unit
from urbanbio.errors import StorageError
from urbanbio.models.run import RunSummary, Waiver
from urbanbio.models.site import Site
from urbanbio.storage import tables

T0 = datetime(2026, 3, 1, 12, 0, tzinfo=UTC)


@pytest.fixture
def store(fs_store: Any) -> Any:
    return fs_store


def waiver(run_id: str, note: str = "battery died") -> Waiver:
    return Waiver(
        waived_stage="ingest",
        waived_unit_id="af-0123456789abcdef",
        reason_code="truncated",
        note=note,
        run_id=run_id,
        created_utc=T0,
    )


WAIVER_UNIT = "ingest:af-0123456789abcdef:truncated"


def write_waiver(store: Any, run_id: str, note: str = "battery died") -> None:
    tables.write_part(
        store,
        "waivers",
        [tables.TableRow(waiver(run_id, note), stage="admin", unit_id=WAIVER_UNIT)],
        name=run_id,
    )


def write_ledger(
    store: Any,
    run_id: str,
    *,
    status: str = "succeeded",
    at: datetime = T0,
    stage: str = "admin",
    unit_id: str = WAIVER_UNIT,
    name: str | None = None,
) -> None:
    reason = None if status == "succeeded" else "truncated"
    row = stage_unit(
        stage=stage,
        unit_id=unit_id,
        run_id=run_id,
        status=status,
        reason_code=reason,
        finished_utc=at,
    )
    tables.write_part(store, "stage_units", [row], name=name or f"{run_id}-0001")


def summary(run_id: str, stage: str, ended: datetime, status: str = "succeeded") -> RunSummary:
    return RunSummary(
        run_id=run_id,
        stage=stage,
        status=status,
        started_utc=ended - timedelta(minutes=5),
        ended_utc=ended,
        package_version="0.0.0",
        git_commit=None,
        git_dirty=True,
        stage_version=1,
        store_id="st-0123456789abcdef",
        params_hash="0" * 64,
        units_total=0,
        skipped=0,
        succeeded=0,
        flagged=0,
        failed=0,
        log_key=f"runs/{run_id}.log",
    )


def rows(conn: Any, sql: str) -> list[tuple]:
    return conn.execute(sql).fetchall()


def read_parquet(store: Any, key: str, tmp_path: Any) -> pq.ParquetFile:
    dest = tmp_path / key.replace("/", "_")
    store.get(key, dest)
    return pq.ParquetFile(io.BytesIO(dest.read_bytes()))


# --- Writing --------------------------------------------------------------------------------


# @spec DATA-PQ-001, DATA-PQ-002, DATA-PQ-004
def test_a_part_file_is_zstd_parquet_with_a_schema_version(store: Any, tmp_path: Any) -> None:
    run_id = "screen-20260301T120000Z-ab12"
    (key,) = tables.write_part(
        store, "stage_units", [stage_unit(run_id=run_id)], name=f"{run_id}-0001"
    )
    assert key == f"curated/stage_units/stage=screen/{run_id}-0001.parquet"
    pf = read_parquet(store, key, tmp_path)
    assert pf.schema_arrow.metadata[b"urbanbio.schema_version"] == b"1.0"
    assert pf.metadata.row_group(0).column(0).compression == "ZSTD"
    # Hive partition values live in the path, not the file.
    assert "stage" not in pf.schema_arrow.names


# @spec DATA-PQ-004
def test_rows_split_into_one_file_per_partition(store: Any) -> None:
    run_id = "archive-20260301T120000Z-ab12"
    keys = tables.write_part(
        store,
        "stage_units",
        [stage_unit(stage="archive", run_id=run_id), stage_unit(stage="purge", run_id=run_id)],
        name=f"{run_id}-0001",
    )
    assert sorted(keys) == [
        f"curated/stage_units/stage=archive/{run_id}-0001.parquet",
        f"curated/stage_units/stage=purge/{run_id}-0001.parquet",
    ]


# @spec DATA-PQ-006
def test_unit_scoped_rows_carry_their_unit_and_run(store: Any, tmp_path: Any) -> None:
    run_id = "admin-20260301T120000Z-ab12"
    (key,) = tables.write_part(
        store,
        "deployments",
        [tables.TableRow(deployment(), stage="admin", unit_id="site1-dep001", run_id=run_id)],
        name=f"site1-dep001-{run_id}",
    )
    assert key == f"curated/deployments/site1-dep001-{run_id}.parquet"
    table = read_parquet(store, key, tmp_path).read()
    assert table.column("stage").to_pylist() == ["admin"]
    assert table.column("unit_id").to_pylist() == ["site1-dep001"]
    assert table.column("run_id").to_pylist() == [run_id]


# @spec DATA-PQ-006
def test_unit_scoped_tables_need_a_unit(store: Any) -> None:
    with pytest.raises(ValueError):
        tables.write_part(store, "waivers", [waiver("admin-20260301T120000Z-ab12")], name="x")


# @spec DATA-PQ-006
def test_run_level_and_ledger_tables_take_plain_rows(store: Any) -> None:
    run_id = "admin-20260301T120000Z-ab12"
    with pytest.raises(ValueError):
        tables.write_part(
            store,
            "stage_units",
            [tables.TableRow(stage_unit(), stage="admin", unit_id="x")],
            name=f"{run_id}-0001",
        )


# @spec DATA-MODEL-004
def test_rows_must_be_the_tables_model(store: Any) -> None:
    with pytest.raises(TypeError):
        tables.write_part(store, "stage_units", [deployment()], name="x")
    with pytest.raises(TypeError):
        tables.write_part(store, "stage_units", [{"stage": "screen"}], name="x")


# @spec DATA-PQ-003
def test_a_part_file_is_never_overwritten(store: Any) -> None:
    run_id = "screen-20260301T120000Z-ab12"
    tables.write_part(store, "stage_units", [stage_unit(run_id=run_id)], name=f"{run_id}-0001")
    with pytest.raises(StorageError) as caught:
        tables.write_part(store, "stage_units", [stage_unit(run_id=run_id)], name=f"{run_id}-0001")
    assert caught.value.reason_code == "store_object_exists"


# --- Views ----------------------------------------------------------------------------------


# @spec DATA-VIEW-001, DATA-VIEW-003
def test_connect_registers_a_view_per_table_even_when_empty(store: Any) -> None:
    conn = tables.connect(store)
    views = {
        name for (name,) in rows(conn, "SELECT view_name FROM duckdb_views() WHERE NOT internal")
    }
    assert set(tables.TABLES) <= views
    for name in tables.TABLES:
        assert rows(conn, f"SELECT count(*) FROM {name}") == [(0,)]
    assert rows(conn, "SELECT count(*) FROM duckdb_tables()") == [(0,)]


# @spec DATA-VIEW-001
def test_views_read_partition_columns(store: Any) -> None:
    write_ledger(
        store, "screen-20260301T120000Z-ab12", stage="screen", unit_id="af-0123456789abcdef"
    )
    conn = tables.connect(store)
    assert rows(conn, "SELECT stage, unit_id FROM stage_units") == [
        ("screen", "af-0123456789abcdef")
    ]


# @spec DATA-VIEW-002
def test_current_views_exist(store: Any) -> None:
    conn = tables.connect(store)
    views = {
        name for (name,) in rows(conn, "SELECT view_name FROM duckdb_views() WHERE NOT internal")
    }
    assert {"waivers_current", "current_sites", "current_deployments"} <= views


# --- Current rows ---------------------------------------------------------------------------


# @spec RUN-CURRENT-001, RUN-CURRENT-006, RUN-RESUME-002
def test_rows_count_only_with_a_succeeded_ledger_row_of_their_run(store: Any) -> None:
    write_waiver(store, "admin-20260301T120000Z-0001")  # orphan: no ledger part
    write_waiver(store, "admin-20260301T120000Z-0002")
    write_ledger(store, "admin-20260301T120000Z-0002", status="failed")
    write_waiver(store, "admin-20260301T120000Z-0003")
    write_ledger(store, "admin-20260301T120000Z-0003")
    conn = tables.connect(store)
    assert rows(conn, "SELECT run_id FROM waivers_current") == [("admin-20260301T120000Z-0003",)]


# @spec RUN-CURRENT-002
def test_the_latest_succeeded_run_of_a_unit_is_current(store: Any) -> None:
    write_waiver(store, "admin-20260301T120000Z-0001", note="first")
    write_ledger(store, "admin-20260301T120000Z-0001", at=T0)
    write_waiver(store, "admin-20260301T120000Z-0002", note="second")
    write_ledger(store, "admin-20260301T120000Z-0002", at=T0 + timedelta(hours=1))
    conn = tables.connect(store)
    assert rows(conn, "SELECT note FROM waivers_current") == [("second",)]


# @spec RUN-CURRENT-003
def test_counting_ignores_the_rows_other_columns(store: Any) -> None:
    # A row's own fields can't make it count: only (stage, unit_id, run_id) against the ledger.
    run_id = "admin-20260301T120000Z-0001"
    tables.write_part(
        store,
        "waivers",
        [tables.TableRow(waiver(run_id), stage="admin", unit_id="ingest:af-ffffffffffffffff:x")],
        name=run_id,
    )
    write_ledger(store, run_id)  # succeeded, but for WAIVER_UNIT
    conn = tables.connect(store)
    assert rows(conn, "SELECT count(*) FROM waivers_current") == [(0,)]


# @spec RUN-CURRENT-004, DATA-PQ-007
def test_run_level_rows_count_once_their_run_has_ended(store: Any) -> None:
    site = Site(
        site_id="site7",
        latitude_generalized_deg=0.0,
        longitude_generalized_deg=0.0,
        coordinate_uncertainty_m=1000,
        generalization="rounded to 0.01°",
        habitat=None,
        timezone="Etc/UTC",
        nws_station_id=None,
    )
    tables.write_part(store, "sites", [site], name="screen-20260301T120000Z-0001")
    conn = tables.connect(store)
    assert rows(conn, "SELECT count(*) FROM current_sites") == [(0,)]
    tables.write_part(
        store,
        "runs",
        [summary("screen-20260301T120000Z-0001", "screen", T0)],
        name="screen-20260301T120000Z-0001",
    )
    conn = tables.connect(store)
    assert rows(conn, "SELECT site_id FROM current_sites") == [("site7",)]


# @spec RUN-CURRENT-004
def test_current_sites_is_the_latest_counted_file(store: Any) -> None:
    def site(habitat: str) -> Site:
        return Site(
            site_id="site7",
            latitude_generalized_deg=0.0,
            longitude_generalized_deg=0.0,
            coordinate_uncertainty_m=1000,
            generalization="rounded to 0.01°",
            habitat=habitat,
            timezone="Etc/UTC",
            nws_station_id=None,
        )

    for i, (habitat, ended) in enumerate([("old", T0), ("new", T0 + timedelta(hours=1))], start=1):
        run_id = f"screen-20260301T120000Z-000{i}"
        tables.write_part(store, "sites", [site(habitat)], name=run_id)
        tables.write_part(store, "runs", [summary(run_id, "screen", ended)], name=run_id)
    tables.write_part(store, "sites", [site("crashed")], name="screen-20260301T120000Z-0009")
    conn = tables.connect(store)
    assert rows(conn, "SELECT habitat FROM current_sites") == [("new",)]


# @spec DATA-DEP-001
def test_current_deployments_takes_the_latest_counted_version(store: Any) -> None:
    def admin(run_id: str, at: datetime, **fields: Any) -> None:
        name = f"site1-dep001-{run_id}"
        tables.write_part(
            store,
            "deployments",
            [
                tables.TableRow(
                    deployment(**fields), stage="admin", unit_id="site1-dep001", run_id=run_id
                )
            ],
            name=name,
        )
        write_ledger(store, run_id, at=at, unit_id="site1-dep001")

    def ingest(run_id: str, ended: datetime | None, **fields: Any) -> None:
        tables.write_part(
            store,
            "deployments",
            [tables.TableRow(deployment(**fields), stage="ingest", unit_id=None, run_id=run_id)],
            name=f"site1-dep001-{run_id}",
        )
        if ended is not None:
            tables.write_part(store, "runs", [summary(run_id, "ingest", ended)], name=run_id)

    admin("admin-20260301T120000Z-0001", T0)
    conn = tables.connect(store)
    assert rows(conn, "SELECT firmware_version FROM current_deployments") == [(None,)]

    ingest("ingest-20260308T120000Z-0002", T0 + timedelta(days=7), firmware_version="1.2.0")
    ingest("ingest-20260315T120000Z-0003", None, firmware_version="1.2.0", timestamp_issues=True)
    conn = tables.connect(store)
    assert rows(conn, "SELECT firmware_version, timestamp_issues FROM current_deployments") == [
        ("1.2.0", False)
    ]

    end = T0 + timedelta(days=30)
    admin("admin-20260331T120000Z-0004", end, firmware_version="1.2.0", end_utc=end)
    conn = tables.connect(store)
    assert rows(conn, "SELECT end_utc FROM current_deployments") == [(end,)]
