import json
from pathlib import Path

import pandas as pd

from src.extract.raw_store import utc_iso, utc_now
from src.transform.curated import CURATED_CONTRACTS_FILE, CuratedError, read_curated_contracts
from src.transform.staging import resolve_pipeline_run_id, sha256_file, write_atomically

PARTITION_FILE = "part-0.parquet"
MANIFEST_FILE = "_manifest.json"
NULL_PARTITION = "__HIVE_DEFAULT_PARTITION__"


class PartitionError(Exception):
    pass


def partition_config(settings):
    config = settings.raw.get("partitioning")
    if not config:
        raise PartitionError("config/settings.yml has no 'partitioning' section")
    return config


def dataset_root(settings):
    return Path(settings.paths["partitioned"]) / partition_config(settings)["dataset"]


def partition_label(value):
    return NULL_PARTITION if value is None or pd.isna(value) else str(int(value))


def partition_keys(frame, config):
    dates = pd.to_datetime(frame[config["date_column"]], errors="coerce")
    return dates.dt.year.astype("Int64"), dates.dt.month.astype("Int64")


def partition_path(config, year, month):
    return f"{config['year_column']}={partition_label(year)}/{config['month_column']}={partition_label(month)}"


def read_manifest(settings):
    path = dataset_root(settings) / MANIFEST_FILE
    if not path.exists():
        raise PartitionError(f"no partitioned dataset at {path.parent}; run 'partition' first")
    return json.loads(path.read_text(encoding="utf-8"))


def build_partitions(settings, run_id=None, rebuild=False, now=utc_now, log=print, pipeline_run_id=None, environ=None):
    config = partition_config(settings)
    try:
        curated_run_id, frame, curated_report = read_curated_contracts(settings, run_id)
    except CuratedError as exc:
        raise PartitionError(str(exc)) from exc
    curated_sha = curated_report["outputs"][f"curated/{CURATED_CONTRACTS_FILE}"]["sha256"]
    root = dataset_root(settings)
    manifest_path = root / MANIFEST_FILE
    if manifest_path.exists() and not rebuild:
        existing = json.loads(manifest_path.read_text(encoding="utf-8"))
        if existing.get("curated_sha256") == curated_sha:
            log(f"Unchanged: curated run {curated_run_id} is already partitioned in {root}; use --rebuild to write it again")
            return existing, "unchanged"

    years, months = partition_keys(frame, config)
    keyed = frame.assign(**{config["year_column"]: years, config["month_column"]: months})
    groups = keyed.groupby([config["year_column"], config["month_column"]], dropna=False, sort=True)
    built_at = now()
    manifest = {
        "status": "complete",
        "dataset": config["dataset"],
        "curated_run_id": curated_run_id,
        "curated_sha256": curated_sha,
        "date_column": config["date_column"],
        "partition_columns": [config["year_column"], config["month_column"]],
        "pipeline_run_id": resolve_pipeline_run_id(pipeline_run_id, environ, built_at),
        "built_at_utc": utc_iso(built_at),
        "rows": int(len(frame)),
        "partitions": [],
    }

    def write(target):
        for (year, month), group in groups:
            relative = partition_path(config, year, month)
            directory = target / relative
            directory.mkdir(parents=True, exist_ok=True)
            part = group.drop(columns=[config["year_column"], config["month_column"]]).sort_values("contract_id").reset_index(drop=True)
            path = directory / PARTITION_FILE
            part.to_parquet(path, index=False)
            manifest["partitions"].append({
                "path": f"{relative}/{PARTITION_FILE}",
                "year": None if pd.isna(year) else int(year),
                "month": None if pd.isna(month) else int(month),
                "rows": int(len(part)),
                "sha256": sha256_file(path),
            })
        if sum(entry["rows"] for entry in manifest["partitions"]) != manifest["rows"]:
            raise PartitionError("row accounting failed: partition rows do not equal curated rows")
        (target / MANIFEST_FILE).write_text(json.dumps(manifest, indent=2, sort_keys=True), encoding="utf-8")

    root.parent.mkdir(parents=True, exist_ok=True)
    write_atomically(root, write)
    dated = sum(entry["rows"] for entry in manifest["partitions"] if entry["year"] is not None)
    log(f"Partitioned curated run {curated_run_id}: {manifest['rows']} rows into {len(manifest['partitions'])} partitions under {root}")
    log(f"Rows with a {config['date_column']}: {dated}; rows in the null partition: {manifest['rows'] - dated}")
    return manifest, "partitioned"


def select_partitions(manifest, year, month=None):
    chosen = [entry for entry in manifest["partitions"] if entry["year"] == year and (month is None or entry["month"] == month)]
    if not chosen:
        label = f"{year}" if month is None else f"{year}-{month}"
        raise PartitionError(f"no partition found for {label}")
    return chosen


def read_partition_slice(settings, year, month=None):
    manifest = read_manifest(settings)
    root = dataset_root(settings)
    frames = []
    for entry in select_partitions(manifest, year, month):
        path = root / entry["path"]
        if sha256_file(path) != entry["sha256"]:
            raise PartitionError(f"checksum mismatch for partition file {entry['path']}; partitioned data was modified")
        frames.append(pd.read_parquet(path))
    return manifest, pd.concat(frames, ignore_index=True)
