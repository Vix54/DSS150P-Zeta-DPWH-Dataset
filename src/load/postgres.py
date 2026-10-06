import io
import json
import re
from pathlib import Path

import pandas as pd
from sqlalchemy import create_engine, text

from src.config import build_db_url
from src.extract.raw_store import utc_iso, utc_now
from src.transform import curated
from src.transform.curated import CuratedError
from src.transform.staging import resolve_pipeline_run_id, sha256_file, write_atomically

LOAD_LANE = "load"
LOAD_QUARANTINE_FILE = "contracts.parquet"
LOAD_REPORT_FILE = "load_report.json"
HASH_PATTERN = r"[0-9a-f]{64}"
IDENTIFIER = re.compile(r"[a-z_][a-z0-9_]*")
QUALIFIED = re.compile(r"[a-z_][a-z0-9_]*\.[a-z_][a-z0-9_]*")
LINEAGE_COLUMNS = ("pipeline_run_id", "loaded_at_utc")


class LoadError(Exception):
    pass


def load_config(settings):
    config = settings.raw.get("load")
    if not config:
        raise LoadError("config/settings.yml has no 'load' section")
    if not QUALIFIED.fullmatch(config["table"]):
        raise LoadError(f"load.table must be schema.table in lower case, found {config['table']!r}")
    for column in [config["key"], *config["columns"]]:
        if not IDENTIFIER.fullmatch(column):
            raise LoadError(f"load column names must be lower-case identifiers, found {column!r}")
    if config["key"] not in config["columns"]:
        raise LoadError("load.key must be one of load.columns")
    return config


def make_engine(settings, environ=None):
    timeout = int(settings.raw["database"]["connect_timeout_seconds"])
    return create_engine(build_db_url(environ), connect_args={"connect_timeout": timeout})


def read_curated_contracts(settings, run_id=None):
    try:
        return curated.read_curated_contracts(settings, run_id)
    except CuratedError as exc:
        raise LoadError(str(exc)) from exc


def rejection_masks(frame, key):
    cost = pd.to_numeric(frame["project_cost"], errors="coerce")
    progress = pd.to_numeric(frame["physical_accomplishment"], errors="coerce")
    status = frame["status_name"].astype("string").str.strip()
    hashes = frame["record_hash"].astype("string")
    keys = frame[key].astype("string").str.strip()
    return {
        "Q_LOAD_MISSING_CONTRACT_ID": (keys.isna() | (keys == "")).fillna(True),
        "Q_LOAD_DUPLICATE_CONTRACT_ID": keys.duplicated(keep=False).fillna(False) & keys.notna(),
        "Q_LOAD_NEGATIVE_PROJECT_COST": (cost.notna() & (cost < 0)).fillna(False),
        "Q_LOAD_PROGRESS_OUT_OF_RANGE": (progress.notna() & ((progress < 0) | (progress > 100))).fillna(False),
        "Q_LOAD_MISSING_STATUS": (status.isna() | (status == "")).fillna(True),
        "Q_LOAD_BAD_RECORD_HASH": ~hashes.str.fullmatch(HASH_PATTERN).fillna(False).astype(bool),
    }


def split_loadable(frame, config):
    masks = rejection_masks(frame, config["key"])
    rejected_mask = pd.Series(False, index=frame.index)
    for mask in masks.values():
        rejected_mask = rejected_mask | mask.astype(bool)
    codes = []
    for position in frame.index[rejected_mask]:
        codes.append("|".join(code for code, mask in masks.items() if bool(mask.loc[position])))
    rejected = frame.loc[rejected_mask].copy()
    rejected.insert(0, "error_codes", pd.Series(codes, index=rejected.index, dtype="string"))
    loadable = frame.loc[~rejected_mask, list(config["columns"])].copy()
    counts = {code: int(mask.sum()) for code, mask in masks.items() if int(mask.sum())}
    return loadable.reset_index(drop=True), rejected.reset_index(drop=True), counts


def csv_buffer(frame):
    out = frame.copy()
    for column in out.columns:
        series = out[column]
        if pd.api.types.is_bool_dtype(series):
            out[column] = series.map(lambda value: None if pd.isna(value) else ("true" if value else "false"))
        elif pd.api.types.is_datetime64_any_dtype(series):
            out[column] = series.map(lambda value: None if pd.isna(value) else value.isoformat())
        elif pd.api.types.is_object_dtype(series):
            out[column] = series.map(lambda value: None if value is None or value is pd.NA or value is pd.NaT else (value.isoformat() if hasattr(value, "isoformat") else value))
    buffer = io.StringIO()
    out.to_csv(buffer, index=False, header=False, na_rep="\\N")
    buffer.seek(0)
    return buffer


def upsert(connection, frame, config, pipeline_run_id, loaded_at):
    table = config["table"]
    key = config["key"]
    columns = list(config["columns"])
    all_columns = columns + list(LINEAGE_COLUMNS)
    batch = frame.loc[:, columns].copy()
    batch["pipeline_run_id"] = pipeline_run_id
    batch["loaded_at_utc"] = pd.Timestamp(loaded_at).tz_convert("UTC").isoformat()
    connection.execute(text(f"CREATE TEMP TABLE load_batch (LIKE {table} INCLUDING DEFAULTS) ON COMMIT DROP"))
    cursor = connection.connection.dbapi_connection.cursor()
    try:
        cursor.copy_expert(
            f"COPY load_batch ({', '.join(all_columns)}) FROM STDIN WITH (FORMAT csv, NULL '\\N')",
            csv_buffer(batch.loc[:, all_columns]),
        )
    finally:
        cursor.close()
    updates = ", ".join(f"{column} = EXCLUDED.{column}" for column in all_columns if column != key)
    statement = text(
        f"""
        WITH upsert AS (
            INSERT INTO {table} AS target ({', '.join(all_columns)})
            SELECT {', '.join(all_columns)} FROM load_batch
            ON CONFLICT ({key}) DO UPDATE SET {updates}
            WHERE target.record_hash IS DISTINCT FROM EXCLUDED.record_hash
            RETURNING (xmax = 0) AS was_inserted
        )
        SELECT
            COUNT(*) FILTER (WHERE was_inserted) AS inserted,
            COUNT(*) FILTER (WHERE NOT was_inserted) AS updated
        FROM upsert
        """
    )
    inserted, updated = connection.execute(statement).one()
    return int(inserted), int(updated)


def write_load_quarantine(settings, curated_run_id, scope, rejected, report):
    directory = Path(settings.paths["quarantine"]) / LOAD_LANE / f"run_id={curated_run_id}" / scope

    def write(target):
        path = target / LOAD_QUARANTINE_FILE
        rejected.to_parquet(path, index=False)
        report["outputs"] = {f"quarantine/{LOAD_LANE}/{scope}/{LOAD_QUARANTINE_FILE}": {"rows": int(len(rejected)), "sha256": sha256_file(path)}}
        (target / LOAD_REPORT_FILE).write_text(json.dumps(report, indent=2, sort_keys=True), encoding="utf-8")

    directory.parent.mkdir(parents=True, exist_ok=True)
    write_atomically(directory, write)
    return directory


def load_frame(settings, frame, curated_run_id, scope, engine=None, environ=None, now=utc_now, log=print, pipeline_run_id=None):
    config = load_config(settings)
    loaded_at = now()
    run_label = resolve_pipeline_run_id(pipeline_run_id, environ, loaded_at)
    loadable, rejected, counts = split_loadable(frame, config)
    rejected["pipeline_run_id"] = run_label
    rejected["quarantined_at_utc"] = pd.Timestamp(loaded_at).tz_convert("UTC")
    own_engine = engine is None
    engine = engine or make_engine(settings, environ)
    try:
        with engine.begin() as connection:
            inserted, updated = upsert(connection, loadable, config, run_label, loaded_at)
    finally:
        if own_engine:
            engine.dispose()
    report = {
        "status": "complete",
        "curated_run_id": curated_run_id,
        "scope": scope,
        "table": config["table"],
        "pipeline_run_id": run_label,
        "loaded_at_utc": utc_iso(loaded_at),
        "rows_read": int(len(frame)),
        "rows_loadable": int(len(loadable)),
        "rows_quarantined": int(len(rejected)),
        "quarantine_counts": counts,
        "inserted": inserted,
        "updated": updated,
        "unchanged": int(len(loadable)) - inserted - updated,
    }
    if report["rows_read"] != report["rows_loadable"] + report["rows_quarantined"]:
        raise LoadError("row accounting failed: loadable plus quarantined rows do not equal rows read")
    write_load_quarantine(settings, curated_run_id, scope, rejected, report)
    log(f"Rows read: {report['rows_read']}, loadable: {report['rows_loadable']}, quarantined: {report['rows_quarantined']}")
    for code, count in counts.items():
        log(f"  quarantine {code}: {count}")
    log(f"Load complete into {config['table']}: inserted={inserted}, updated={updated}, unchanged={report['unchanged']}")
    return report


def load_to_postgres(settings, run_id=None, engine=None, environ=None, now=utc_now, log=print, pipeline_run_id=None):
    curated_run_id, frame, _ = read_curated_contracts(settings, run_id)
    log(f"Loading curated run {curated_run_id} ({len(frame)} contracts, checksum verified)")
    return load_frame(settings, frame, curated_run_id, "scope=full", engine=engine, environ=environ, now=now, log=log, pipeline_run_id=pipeline_run_id)


def apply_schema(settings, engine=None, environ=None, log=print):
    sql_dir = Path(settings.paths["sql_init"])
    files = sorted(sql_dir.glob("*.sql"))
    if not files:
        raise LoadError(f"no SQL files found in {sql_dir}")
    own_engine = engine is None
    engine = engine or make_engine(settings, environ)
    try:
        with engine.begin() as connection:
            for path in files:
                connection.exec_driver_sql(path.read_text(encoding="utf-8"))
                log(f"Applied {path.name}")
    finally:
        if own_engine:
            engine.dispose()
    return [path.name for path in files]
