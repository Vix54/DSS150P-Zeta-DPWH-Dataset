import json
from pathlib import Path

import numpy as np
import pandas as pd
import pyarrow.parquet as pq
from sqlalchemy import text

from src.extract.file_source import lane_root
from src.extract.raw_store import RawRun, RawStoreError
from src.transform import curated, partition, staging

PASS = "PASS"
FAIL = "FAIL"
WARN = "WARN"
SKIP = "SKIP"


class Results:
    def __init__(self):
        self.items = []

    def add(self, ok, message, otherwise=FAIL):
        self.items.append((PASS if ok else otherwise, message))
        return ok

    def note(self, status, message):
        self.items.append((status, message))

    def failed(self):
        return sum(1 for status, _ in self.items if status == FAIL)


def parquet_rows(path):
    return pq.ParquetFile(path).metadata.num_rows


def check_outputs(results, report, locations, label):
    for key, entry in sorted(report.get("outputs", {}).items()):
        path = None
        for prefix, directory in locations:
            if key.startswith(prefix):
                path = Path(directory) / key[len(prefix):]
                break
        if path is None or not path.exists():
            results.add(False, f"{label}: output {key} is missing")
            continue
        if staging.sha256_file(path) != entry["sha256"]:
            results.add(False, f"{label}: {key} does not match its recorded SHA-256 (file was modified)")
            continue
        same_rows = path.suffix != ".parquet" or parquet_rows(path) == entry["rows"]
        results.add(same_rows, f"{label}: {key} matches its recorded SHA-256 and row count ({entry['rows']} rows)")


def validate_raw(settings, results):
    runs = {}
    for name, config in sorted((settings.raw.get("file_sources") or {}).items()):
        root = lane_root(settings.paths["raw"], name)
        try:
            run_id = staging.latest_run_id(root)
        except staging.StagingError:
            results.add(False, f"Raw {name}: no raw run found under {root}")
            continue
        run = RawRun.open_existing(root, run_id)
        try:
            verified = run.verified_files()
            results.add(bool(verified), f"Raw {name} run {run_id}: {len(verified)} file(s) match manifest.jsonl")
        except RawStoreError as exc:
            results.add(False, f"Raw {name} run {run_id}: {exc}")
            continue
        expected = str(config["expected_sha256"]).strip().lower()
        recorded = {entry["sha256"] for entry in run.manifest_entries()}
        results.add(expected in recorded, f"Raw {name} run {run_id}: stored file hash equals expected_sha256 in config/settings.yml")
        runs[name] = run
    return runs


def validate_staging(settings, results, raw_runs):
    primary = settings.raw["curated"]["primary_source"]
    root = lane_root(settings.paths["staging"], primary)
    try:
        run_id = staging.latest_run_id(root)
    except staging.StagingError:
        results.add(False, f"Staging: no staging run found under {root}")
        return None
    directory = root / f"run_id={run_id}"
    report_path = directory / staging.REPORT_FILE
    if not report_path.exists():
        results.add(False, f"Staging run {run_id}: {staging.REPORT_FILE} is missing")
        return None
    report = json.loads(report_path.read_text(encoding="utf-8"))
    quarantine_dir = lane_root(settings.paths["quarantine"], primary) / f"run_id={run_id}"
    check_outputs(results, report, [("staging/", directory), ("quarantine/", quarantine_dir)], f"Staging run {run_id}")
    raw_run = raw_runs.get(primary)
    if raw_run is None:
        results.add(False, f"Reconciliation raw -> staging: not checked because the raw {primary} run failed verification")
    elif raw_run.run_id == run_id:
        raw_rows = parquet_rows(raw_run.directory / report["raw_file"])
        results.add(raw_rows == report["rows_in"], f"Reconciliation raw -> staging: raw rows {raw_rows} = staging rows in {report['rows_in']}")
    else:
        results.add(False, f"Reconciliation raw -> staging: staging run {run_id} does not match the latest raw run")
    total = report["rows_staged"] + report["rows_quarantined"]
    results.add(total == report["rows_in"], f"Reconciliation staging: staged {report['rows_staged']} + quarantined {report['rows_quarantined']} = rows in {report['rows_in']}")
    return run_id, report


def validate_curated(settings, results, staging_result):
    try:
        run_id, report = curated.read_curated_report(settings)
    except curated.CuratedError as exc:
        results.add(False, f"Curated: {exc}")
        return None
    directory = Path(settings.paths["curated"]) / f"run_id={run_id}"
    quarantine_dir = Path(settings.paths["quarantine"]) / curated.CURATED_LANE / f"run_id={run_id}"
    check_outputs(results, report, [("curated/", directory), ("quarantine/curated/", quarantine_dir)], f"Curated run {run_id}")
    if staging_result is not None:
        staging_run_id, staging_report = staging_result
        results.add(report["staging_run_id"] == staging_run_id, f"Lineage: curated run was built from staging run {staging_run_id}")
        results.add(report["rows_in"] == staging_report["rows_staged"], f"Reconciliation staging -> curated: staged rows {staging_report['rows_staged']} = curated rows in {report['rows_in']}")
    total = report["rows_curated"] + report["rows_quarantined"]
    results.add(total == report["rows_in"], f"Reconciliation curated: curated {report['rows_curated']} + quarantined {report['rows_quarantined']} = rows in {report['rows_in']}")
    contracts = directory / curated.CURATED_CONTRACTS_FILE
    intact = contracts.exists() and staging.sha256_file(contracts) == report["outputs"][f"curated/{curated.CURATED_CONTRACTS_FILE}"]["sha256"]
    return run_id, report, intact


def same_numbers(left, right, tolerance=1e-6):
    left = pd.to_numeric(left, errors="coerce").astype("Float64")
    right = pd.to_numeric(right, errors="coerce").astype("Float64")
    both_null = left.isna() & right.isna()
    close = (left - right).abs().fillna(np.inf) <= tolerance * np.maximum(1.0, right.abs().fillna(0).astype(float))
    return bool((both_null | close.fillna(False)).all())


def validate_calculations(settings, results, frame):
    ids = frame["contract_id"].astype("string")
    results.add(ids.notna().all() and not ids.duplicated().any(), f"Invariant: contract_id is present and unique in all {len(frame)} curated rows")
    progress = pd.to_numeric(frame["physical_accomplishment"], errors="coerce")
    results.add(bool(((progress >= 0) & (progress <= 100)).fillna(True).all()), "Invariant: physical_accomplishment is within 0 to 100 wherever present")
    recomputed = curated.record_hashes(frame)
    mismatched = int((recomputed != frame["record_hash"].astype("string")).sum())
    results.add(mismatched == 0, f"Invariant: record_hash recomputed from business columns matches for every row ({mismatched} mismatches)")
    abc = frame["abc_php"].astype("Float64")
    award = frame["award_amount_php"].astype("Float64")
    both = abc.notna() & award.notna()
    results.add(same_numbers(frame["award_savings_php"], (abc - award).where(both)), "Invariant: award_savings_php = abc_php - award_amount_php")
    results.add(same_numbers(frame["award_to_abc_pct"], (award / abc * 100).where(both & (abc > 0)).round(4)), "Invariant: award_to_abc_pct = award_amount_php / abc_php * 100")
    as_of = pd.Timestamp(frame["delay_as_of_date"].dropna().iloc[0]) if frame["delay_as_of_date"].notna().any() else None
    if as_of is None:
        results.add(len(frame) == 0, "Invariant: delay_as_of_date is recorded")
    else:
        expiry = pd.to_datetime(frame["expiry_date"])
        delayed = (frame["status_name"] == curated.DELAY_STATUS) & expiry.notna() & (expiry < as_of) & (progress < 100)
        expected = delayed.fillna(False).astype(bool)
        actual = frame["is_delayed"].fillna(False).astype(bool)
        results.add(bool((expected == actual).all()), f"Invariant: is_delayed follows the delay rule as of {as_of.date()} ({int(actual.sum())} delayed)")
    matches = frame["region_matches_psgc"].fillna(False).astype(bool)
    results.add(bool((matches == frame["region_psgc_code"].notna()).all()), "Invariant: region_matches_psgc agrees with region_psgc_code")


def validate_partitions(settings, results, curated_report):
    try:
        manifest = partition.read_manifest(settings)
    except partition.PartitionError:
        results.note(SKIP, "Partitions: no partitioned dataset yet (run 'partition')")
        return
    curated_sha = curated_report["outputs"][f"curated/{curated.CURATED_CONTRACTS_FILE}"]["sha256"]
    if manifest["curated_sha256"] == curated_sha:
        results.add(True, f"Partitions: built from the current curated file (curated run {manifest['curated_run_id']})")
    else:
        results.note(WARN, "Partitions: built from an earlier version of the curated file; run 'partition' to refresh them")
    root = partition.dataset_root(settings)
    bad = [entry["path"] for entry in manifest["partitions"] if not (root / entry["path"]).exists() or staging.sha256_file(root / entry["path"]) != entry["sha256"]]
    results.add(not bad, f"Partitions: all {len(manifest['partitions'])} partition files match _manifest.json")
    total = sum(entry["rows"] for entry in manifest["partitions"])
    results.add(total == curated_report["rows_curated"], f"Reconciliation curated -> partitions: {total} partition rows = {curated_report['rows_curated']} curated rows")


def validate_database(settings, results, frame, year=None, month=None, engine=None, environ=None):
    from src.load.postgres import load_config, make_engine, split_loadable

    config = load_config(settings)
    expected, _, _ = split_loadable(frame, config)
    scope = "all contracts"
    if year is not None:
        years, months = partition.partition_keys(expected, partition.partition_config(settings))
        mask = years == year
        if month is not None:
            mask = mask & (months == month)
        expected = expected.loc[mask.fillna(False).to_numpy()]
        scope = f"partition {year}" + ("" if month is None else f"-{month}")
    own_engine = engine is None
    engine = engine or make_engine(settings, environ)
    try:
        with engine.connect() as connection:
            rows = connection.execute(text(f"SELECT {config['key']}, record_hash FROM {config['table']}")).all()
    except Exception as exc:
        reason = str(exc).strip().splitlines()[0] if str(exc).strip() else type(exc).__name__
        results.add(False, f"Database: could not read {config['table']}: {reason}")
        return
    finally:
        if own_engine:
            engine.dispose()
    in_db = {key: value.strip() for key, value in rows}
    wanted = dict(zip(expected[config["key"]].astype(str), expected["record_hash"].astype(str)))
    missing = [key for key in wanted if key not in in_db]
    differing = [key for key, value in wanted.items() if key in in_db and in_db[key] != value]
    results.add(not missing, f"Database ({scope}): every loadable curated contract is in {config['table']} ({len(missing)} missing of {len(wanted)})")
    results.add(not differing, f"Database ({scope}): record_hash in the database matches the curated file ({len(differing)} differ)")
    if year is None:
        extra = [key for key in in_db if key not in wanted]
        results.add(not extra, f"Database: no rows outside the current curated run ({len(extra)} extra)", otherwise=WARN)


def run_validation(settings, skip_db=False, year=None, month=None, engine=None, environ=None, log=print):
    results = Results()
    raw_runs = validate_raw(settings, results)
    staging_result = validate_staging(settings, results, raw_runs)
    curated_result = validate_curated(settings, results, staging_result)
    if curated_result is not None and not curated_result[2]:
        results.note(SKIP, "Calculation, partition and database checks skipped because the curated contracts file failed its checksum")
    elif curated_result is not None:
        run_id, report, _ = curated_result
        frame = pd.read_parquet(curated.curated_contracts_path(settings, run_id))
        validate_calculations(settings, results, frame)
        validate_partitions(settings, results, report)
        if skip_db:
            results.note(SKIP, "Database checks skipped (--skip-db)")
        else:
            validate_database(settings, results, frame, year=year, month=month, engine=engine, environ=environ)
    for status, message in results.items:
        log(f"[{status}] {message}")
    counts = {status: sum(1 for item, _ in results.items if item == status) for status in (PASS, FAIL, WARN, SKIP)}
    log(f"Result: {counts[PASS]} passed, {counts[FAIL]} failed, {counts[WARN]} warnings, {counts[SKIP]} skipped")
    return results
