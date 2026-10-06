import json
import os
from datetime import datetime, timezone

import pandas as pd
import pytest

from src.cli import build_parser
from src.config import PROJECT_ROOT, Settings
from src.load import postgres
from src.transform import curated, partition
from src.validate import pipeline
from tests.test_curated import Clock, contract_frame, make_psgc_run
from tests.test_staging import make_raw_run, row
from src.transform import staging

FIXED = datetime(2026, 10, 7, 2, 0, 0, tzinfo=timezone.utc)
LOAD = {
    "table": "curated.dpwh_projects",
    "key": "contract_id",
    "columns": ["contract_id", "project_cost", "physical_accomplishment", "start_date", "infra_year", "is_delayed", "status_name", "record_hash"],
    "audit_table": "audit.partition_loads",
}
PARTITIONING = {"dataset": "dpwh_contracts", "date_column": "start_date", "year_column": "start_year", "month_column": "start_month"}


def silent(message):
    return None


def make_settings(tmp_path, raw_sha="0" * 64, psgc_sha="0" * 64):
    raw = {
        "file_sources": {
            "bettergov_hf": {"revision_date_utc": "2026-01-22T00:40:18Z", "expected_sha256": raw_sha},
            "psa_psgc": {"expected_sha256": psgc_sha},
        },
        "curated": {
            "primary_source": "bettergov_hf",
            "reference_source": "psa_psgc",
            "psgc_sheet": "PSGC",
            "psgc_code_column": "10-digit PSGC",
            "psgc_name_column": "Name",
            "psgc_level_column": "Geographic Level",
            "psgc_region_level": "Reg",
            "region_aliases": {"Region IV-B": "MIMAROPA"},
        },
        "database": {"schema_curated": "curated", "schema_audit": "audit", "connect_timeout_seconds": 5},
        "load": LOAD,
        "partitioning": PARTITIONING,
        "benchmark": {"runs": 2, "filter_column": "status_name", "filter_value": "On-Going"},
        "analytics": {"target": "is_delayed", "population_status": "On-Going", "test_size": 0.2, "cv_folds": 3, "top_categories": 10},
    }
    names = ("raw", "staging", "curated", "quarantine", "partitioned", "benchmarks", "analytics")
    paths = {name: tmp_path / name for name in names}
    paths["sql_init"] = PROJECT_ROOT / "sql" / "init"
    return Settings(raw=raw, paths=paths)


def frame_with_costs():
    frame = contract_frame()
    extra = pd.DataFrame(
        [
            row(contractId="Z0000001", budget=None, startDate=None),
            row(contractId="Z0000002", budget=-5.0, startDate="2021-03-15"),
        ],
        columns=list(staging.SOURCE_COLUMNS),
    )
    return pd.concat([frame, extra], ignore_index=True)


def build_pipeline(tmp_path, frame=None):
    raw_run = make_raw_run(tmp_path, frame_with_costs() if frame is None else frame)
    psgc_run = make_psgc_run(tmp_path)
    settings = make_settings(tmp_path, raw_run.manifest_entries()[0]["sha256"], psgc_run.manifest_entries()[0]["sha256"])
    staging.run_staging(settings, now=lambda: FIXED, log=silent, environ={})
    curated.run_curated(settings, now=Clock(), log=silent, environ={})
    return settings


def curated_frame(settings):
    return curated.read_curated_contracts(settings)[1]


def test_split_loadable_quarantines_rows_the_table_rejects_and_keeps_null_costs(tmp_path):
    settings = build_pipeline(tmp_path)
    frame = curated_frame(settings)
    loadable, rejected, counts = postgres.split_loadable(frame, LOAD)
    assert counts == {"Q_LOAD_NEGATIVE_PROJECT_COST": 1}
    assert rejected["contract_id"].tolist() == ["Z0000002"]
    assert rejected["error_codes"].tolist() == ["Q_LOAD_NEGATIVE_PROJECT_COST"]
    assert "Z0000001" in set(loadable["contract_id"])
    assert loadable.loc[loadable["contract_id"] == "Z0000001", "project_cost"].isna().all()
    assert list(loadable.columns) == LOAD["columns"]
    assert len(loadable) + len(rejected) == len(frame)


def test_split_loadable_flags_duplicates_missing_status_and_bad_hashes():
    frame = pd.DataFrame(
        {
            "contract_id": ["A", "A", "B", "C"],
            "project_cost": [1.0, 2.0, 3.0, 4.0],
            "physical_accomplishment": [10.0, 10.0, 10.0, 150.0],
            "start_date": [None] * 4,
            "infra_year": [2020] * 4,
            "is_delayed": [False] * 4,
            "status_name": ["Completed", "Completed", None, "Completed"],
            "record_hash": ["a" * 64, "b" * 64, "c" * 64, "not-a-hash"],
        }
    )
    _, rejected, counts = postgres.split_loadable(frame, LOAD)
    assert counts == {
        "Q_LOAD_DUPLICATE_CONTRACT_ID": 2,
        "Q_LOAD_PROGRESS_OUT_OF_RANGE": 1,
        "Q_LOAD_MISSING_STATUS": 1,
        "Q_LOAD_BAD_RECORD_HASH": 1,
    }
    assert rejected.loc[rejected["contract_id"] == "C", "error_codes"].iloc[0] == "Q_LOAD_PROGRESS_OUT_OF_RANGE|Q_LOAD_BAD_RECORD_HASH"


def test_original_curated_frame_is_not_mutated_by_split(tmp_path):
    settings = build_pipeline(tmp_path)
    frame = curated_frame(settings)
    before = frame.copy()
    postgres.split_loadable(frame, LOAD)
    pd.testing.assert_frame_equal(frame, before)


def test_csv_buffer_writes_nulls_booleans_and_dates_for_copy():
    frame = pd.DataFrame(
        {
            "flag": pd.array([True, None], dtype="boolean"),
            "day": [pd.Timestamp("2022-02-24").date(), None],
            "amount": pd.array([1.5, None], dtype="Float64"),
        }
    )
    lines = postgres.csv_buffer(frame).read().splitlines()
    assert lines == ["true,2022-02-24,1.5", "\\N,\\N,\\N"]


def test_load_config_rejects_unsafe_identifiers(tmp_path):
    settings = make_settings(tmp_path)
    settings.raw["load"] = {**LOAD, "table": "curated.dpwh_projects; DROP TABLE x"}
    with pytest.raises(postgres.LoadError):
        postgres.load_config(settings)


def test_partitions_cover_every_curated_row_with_a_null_partition(tmp_path):
    settings = build_pipeline(tmp_path)
    manifest, status = partition.build_partitions(settings, now=lambda: FIXED, log=silent, environ={})
    assert status == "partitioned"
    assert manifest["rows"] == len(curated_frame(settings))
    assert sum(entry["rows"] for entry in manifest["partitions"]) == manifest["rows"]
    paths = {entry["path"] for entry in manifest["partitions"]}
    assert "start_year=2022/start_month=2/part-0.parquet" in paths
    assert f"start_year={partition.NULL_PARTITION}/start_month={partition.NULL_PARTITION}/part-0.parquet" in paths
    part = pd.read_parquet(partition.dataset_root(settings) / "start_year=2021/start_month=3/part-0.parquet")
    assert part["contract_id"].tolist() == ["Z0000002"]
    assert "start_year" not in part.columns


def test_partition_rerun_is_unchanged_and_slices_are_checksummed(tmp_path):
    settings = build_pipeline(tmp_path)
    partition.build_partitions(settings, now=lambda: FIXED, log=silent, environ={})
    _, status = partition.build_partitions(settings, now=lambda: FIXED, log=silent, environ={})
    assert status == "unchanged"
    manifest, frame = partition.read_partition_slice(settings, 2022)
    assert set(frame["contract_id"]) >= {"A0000001"}
    with pytest.raises(partition.PartitionError):
        partition.read_partition_slice(settings, 1999)
    target = partition.dataset_root(settings) / "start_year=2022/start_month=2/part-0.parquet"
    target.write_bytes(target.read_bytes() + b"x")
    with pytest.raises(partition.PartitionError, match="checksum mismatch"):
        partition.read_partition_slice(settings, 2022, 2)


def test_validation_passes_on_a_clean_pipeline_without_database(tmp_path):
    settings = build_pipeline(tmp_path)
    partition.build_partitions(settings, now=lambda: FIXED, log=silent, environ={})
    results = pipeline.run_validation(settings, skip_db=True, log=silent)
    assert results.failed() == 0
    messages = " ".join(message for _, message in results.items)
    assert "record_hash recomputed" in messages
    assert "Reconciliation staging -> curated" in messages


def test_validation_fails_when_a_layer_is_modified(tmp_path):
    settings = build_pipeline(tmp_path)
    run_id, _ = curated.read_curated_report(settings)
    target = settings.paths["curated"] / f"run_id={run_id}" / curated.CURATED_CONTRACTS_FILE
    target.write_bytes(target.read_bytes() + b"x")
    results = pipeline.run_validation(settings, skip_db=True, log=silent)
    assert results.failed() >= 1
    assert any("does not match its recorded SHA-256" in message for status, message in results.items if status == pipeline.FAIL)


def test_validation_fails_when_expected_hash_differs(tmp_path):
    settings = build_pipeline(tmp_path)
    settings.raw["file_sources"]["bettergov_hf"]["expected_sha256"] = "f" * 64
    results = pipeline.run_validation(settings, skip_db=True, log=silent)
    assert any("expected_sha256" in message for status, message in results.items if status == pipeline.FAIL)


def test_cli_downstream_commands_parse():
    parser = build_parser()
    assert parser.parse_args(["load"]).run_id is None
    args = parser.parse_args(["load-partition", "--year", "2022", "--month", "2"])
    assert (args.year, args.month) == (2022, 2)
    with pytest.raises(SystemExit):
        parser.parse_args(["load-partition", "--year", "2022", "--month", "13"])
    assert parser.parse_args(["validate", "--skip-db"]).skip_db is True
    assert parser.parse_args(["benchmark"]).skip_db is False
    assert parser.parse_args(["partition", "--rebuild"]).rebuild is True
    assert parser.parse_args(["extract-file", "--source", "all"]).source == "all"
    for command in ("init-db", "analyze"):
        assert parser.parse_args([command]).handler is not None


def test_benchmarks_without_database_write_results(tmp_path):
    from src.benchmark import formats

    settings = build_pipeline(tmp_path)
    detail = formats.run_benchmarks(settings, skip_db=True, now=lambda: FIXED, log=silent, environ={})
    names = [entry["format"] for entry in detail["results"]]
    assert names == ["CSV", "JSON Lines", "Parquet (Snappy)", "Parquet (Zstandard)"]
    assert all(len(entry["full_read_runs_s"]) == 2 for entry in detail["results"])
    assert len({entry["rows_filtered"] for entry in detail["results"]}) == 1
    assert (settings.paths["benchmarks"] / formats.RESULTS_FILE).exists()


def test_analytics_builds_insights_from_computed_numbers(tmp_path):
    from src.analytics import full_analysis

    settings = build_pipeline(tmp_path)
    summary, metrics = full_analysis.run_analytics(settings, log=silent)
    insights = (settings.paths["analytics"] / "insights.md").read_text(encoding="utf-8")
    assert metrics is None
    assert f"{summary['ongoing']:,} of {summary['rows']:,} curated contracts are On-Going" in insights
    assert "10/10" not in insights
    assert "physical_accomplishment" not in full_analysis.NUMERIC_FEATURES + full_analysis.CATEGORICAL_FEATURES


DB_READY = (
    all(os.environ.get(name) for name in ("POSTGRES_HOST", "POSTGRES_PORT", "POSTGRES_DB", "POSTGRES_USER", "POSTGRES_PASSWORD"))
    and os.environ.get("POSTGRES_DB", "").endswith("_test")
    and os.environ.get("ZETA_TEST_DB") == os.environ.get("POSTGRES_DB")
)


@pytest.mark.skipif(not DB_READY, reason="database test drops the curated and audit schemas; set POSTGRES_DB to a scratch database ending in _test and ZETA_TEST_DB to the same name")
def test_database_load_is_idempotent_and_partition_loads_are_audited(tmp_path):
    from sqlalchemy import text

    from src.load import partitions

    settings = build_pipeline(tmp_path)
    engine = postgres.make_engine(settings)
    with engine.begin() as connection:
        connection.execute(text("DROP SCHEMA IF EXISTS curated CASCADE"))
        connection.execute(text("DROP SCHEMA IF EXISTS audit CASCADE"))
    postgres.apply_schema(settings, engine=engine, log=silent)
    first = postgres.load_to_postgres(settings, engine=engine, now=lambda: FIXED, log=silent, environ={})
    second = postgres.load_to_postgres(settings, engine=engine, now=lambda: FIXED, log=silent, environ={})
    assert first["inserted"] == first["rows_loadable"] and first["updated"] == 0
    assert (second["inserted"], second["updated"]) == (0, 0)
    assert first["rows_quarantined"] == 1
    partition.build_partitions(settings, now=lambda: FIXED, log=silent, environ={})
    report = partitions.load_partition(settings, 2022, 2, engine=engine, now=lambda: FIXED, log=silent, environ={})
    assert (report["inserted"], report["updated"]) == (0, 0)
    with pytest.raises(partition.PartitionError):
        partitions.load_partition(settings, 1999, engine=engine, now=lambda: FIXED, log=silent, environ={})
    with engine.connect() as connection:
        audit = connection.execute(text("SELECT status, partition_path FROM audit.partition_loads ORDER BY load_id")).all()
    assert [tuple(item) for item in audit] == [("success", "start_year=2022/start_month=2"), ("failed", "start_year=1999")]
    results = pipeline.run_validation(settings, engine=engine, log=silent)
    assert results.failed() == 0
    with engine.begin() as connection:
        connection.execute(text("UPDATE curated.dpwh_projects SET record_hash = repeat('0', 64) WHERE contract_id = 'A0000001'"))
    assert pipeline.run_validation(settings, engine=engine, log=silent).failed() == 1
    third = postgres.load_to_postgres(settings, engine=engine, now=lambda: FIXED, log=silent, environ={})
    assert (third["inserted"], third["updated"]) == (0, 1)
    engine.dispose()
