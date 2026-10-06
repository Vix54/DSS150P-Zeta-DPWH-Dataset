import json
import os
import platform
import statistics
import time
from pathlib import Path

import pandas as pd
from sqlalchemy import text

from src.extract.raw_store import utc_iso, utc_now
from src.transform.curated import read_curated_contracts
from src.transform.staging import resolve_pipeline_run_id

RESULTS_FILE = "benchmark_results.csv"
DETAIL_FILE = "benchmark_runs.json"
BENCH_TABLE = "bench_curated_contracts"


def benchmark_config(settings):
    config = settings.raw.get("benchmark") or {}
    return {
        "runs": int(config.get("runs", 5)),
        "filter_column": config.get("filter_column", "status_name"),
        "filter_value": config.get("filter_value", "On-Going"),
    }


def timed(func, runs):
    durations = []
    result = None
    for _ in range(runs):
        start = time.perf_counter()
        result = func()
        durations.append(time.perf_counter() - start)
    return durations, result


def text_ready(frame):
    out = frame.copy()
    for column in out.columns:
        if pd.api.types.is_object_dtype(out[column]):
            out[column] = out[column].map(lambda value: value.isoformat() if hasattr(value, "isoformat") else value)
    return out


def file_formats(directory, column, value):
    def csv_write(frame, path):
        frame.to_csv(path, index=False)

    def csv_read(path):
        return pd.read_csv(path, low_memory=False)

    def csv_filter(path):
        frame = pd.read_csv(path, low_memory=False)
        return frame.loc[frame[column] == value]

    def jsonl_write(frame, path):
        frame.to_json(path, orient="records", lines=True, date_format="iso")

    def jsonl_read(path):
        return pd.read_json(path, orient="records", lines=True)

    def jsonl_filter(path):
        frame = pd.read_json(path, orient="records", lines=True)
        return frame.loc[frame[column] == value]

    def parquet(compression):
        return (
            lambda frame, path: frame.to_parquet(path, index=False, compression=compression),
            lambda path: pd.read_parquet(path),
            lambda path: pd.read_parquet(path, filters=[(column, "==", value)]),
        )

    return [
        ("CSV", directory / "bench.csv", csv_write, csv_read, csv_filter),
        ("JSON Lines", directory / "bench.jsonl", jsonl_write, jsonl_read, jsonl_filter),
        ("Parquet (Snappy)", directory / "bench_snappy.parquet", *parquet("snappy")),
        ("Parquet (Zstandard)", directory / "bench_zstd.parquet", *parquet("zstd")),
    ]


def summarise(name, write_times, read_times, filter_times, size_bytes, rows_filtered):
    return {
        "format": name,
        "size_bytes": int(size_bytes),
        "size_mb": round(size_bytes / 1e6, 3),
        "write_median_s": round(statistics.median(write_times), 4),
        "full_read_median_s": round(statistics.median(read_times), 4),
        "filtered_query_median_s": round(statistics.median(filter_times), 4),
        "rows_filtered": int(rows_filtered),
        "write_runs_s": [round(value, 4) for value in write_times],
        "full_read_runs_s": [round(value, 4) for value in read_times],
        "filtered_query_runs_s": [round(value, 4) for value in filter_times],
    }


def benchmark_files(frame, directory, config, log):
    results = []
    for name, path, write, read, query in file_formats(directory, config["filter_column"], config["filter_value"]):
        log(f"Benchmarking {name} ({config['runs']} runs each for write, full read and filtered query)")
        source = text_ready(frame) if name in ("CSV", "JSON Lines") else frame
        write_times, _ = timed(lambda: write(source, path), config["runs"])
        read_times, _ = timed(lambda: read(path), config["runs"])
        filter_times, filtered = timed(lambda: query(path), config["runs"])
        results.append(summarise(name, write_times, read_times, filter_times, os.path.getsize(path), len(filtered)))
        path.unlink()
    return results


def benchmark_postgres(frame, settings, config, log, engine=None, environ=None):
    from src.load.postgres import csv_buffer, make_engine

    schema = settings.raw["database"]["schema_curated"]
    table = f"{schema}.{BENCH_TABLE}"
    column = config["filter_column"]
    own_engine = engine is None
    engine = engine or make_engine(settings, environ)
    source = text_ready(frame)

    def write():
        with engine.begin() as connection:
            connection.execute(text(f"DROP TABLE IF EXISTS {table}"))
            source.head(0).to_sql(BENCH_TABLE, connection, schema=schema, index=False)
            cursor = connection.connection.dbapi_connection.cursor()
            try:
                cursor.copy_expert(
                    f"COPY {table} ({', '.join(chr(34) + name + chr(34) for name in source.columns)}) FROM STDIN WITH (FORMAT csv, NULL '\\N')",
                    csv_buffer(source),
                )
            finally:
                cursor.close()

    def read():
        with engine.connect() as connection:
            return pd.read_sql(text(f"SELECT * FROM {table}"), connection)

    def query():
        with engine.connect() as connection:
            return pd.read_sql(text(f'SELECT * FROM {table} WHERE "{column}" = :value'), connection, params={"value": config["filter_value"]})

    try:
        log(f"Benchmarking PostgreSQL ({config['runs']} runs each for write, full read and filtered query)")
        write_times, _ = timed(write, config["runs"])
        read_times, _ = timed(read, config["runs"])
        filter_times, filtered = timed(query, config["runs"])
        with engine.connect() as connection:
            size = connection.execute(text("SELECT pg_total_relation_size(CAST(:name AS regclass))"), {"name": table}).scalar()
        return summarise("PostgreSQL", write_times, read_times, filter_times, size, len(filtered))
    finally:
        with engine.begin() as connection:
            connection.execute(text(f"DROP TABLE IF EXISTS {table}"))
        if own_engine:
            engine.dispose()


def run_benchmarks(settings, run_id=None, skip_db=False, engine=None, environ=None, now=utc_now, log=print, pipeline_run_id=None):
    config = benchmark_config(settings)
    curated_run_id, frame, _ = read_curated_contracts(settings, run_id)
    directory = Path(settings.paths["benchmarks"])
    directory.mkdir(parents=True, exist_ok=True)
    started_at = now()
    log(f"Benchmarking curated run {curated_run_id}: {len(frame)} rows x {frame.shape[1]} columns, filter {config['filter_column']} = {config['filter_value']!r}")
    results = benchmark_files(frame, directory, config, log)
    if skip_db:
        log("PostgreSQL benchmark skipped (--skip-db)")
    else:
        results.append(benchmark_postgres(frame, settings, config, log, engine=engine, environ=environ))
    detail = {
        "curated_run_id": curated_run_id,
        "pipeline_run_id": resolve_pipeline_run_id(pipeline_run_id, environ, started_at),
        "started_at_utc": utc_iso(started_at),
        "finished_at_utc": utc_iso(now()),
        "rows": int(len(frame)),
        "columns": int(frame.shape[1]),
        "runs_per_measure": config["runs"],
        "statistic": "median",
        "filter": {"column": config["filter_column"], "value": config["filter_value"]},
        "environment": {
            "python": platform.python_version(),
            "platform": platform.platform(),
            "processor": platform.processor() or platform.machine(),
            "cpu_count": os.cpu_count(),
            "pandas": pd.__version__,
            "in_container": os.path.exists("/.dockerenv"),
        },
        "results": results,
    }
    table = pd.DataFrame([{key: value for key, value in entry.items() if not key.endswith("_runs_s")} for entry in results])
    table.to_csv(directory / RESULTS_FILE, index=False)
    (directory / DETAIL_FILE).write_text(json.dumps(detail, indent=2), encoding="utf-8")
    log(table.to_string(index=False))
    log(f"Results saved to {directory / RESULTS_FILE} and {directory / DETAIL_FILE}")
    return detail
