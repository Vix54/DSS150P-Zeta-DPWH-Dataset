from sqlalchemy import text

from src.extract.raw_store import utc_now
from src.load.postgres import load_config, load_frame, make_engine
from src.transform.partition import partition_config, partition_path, read_partition_slice
from src.transform.staging import resolve_pipeline_run_id


def partition_scope(settings, year, month=None):
    config = partition_config(settings)
    if month is None:
        return f"{config['year_column']}={int(year)}"
    return partition_path(config, year, month)


def write_audit(engine, table, values):
    columns = list(values)
    statement = text(f"INSERT INTO {table} ({', '.join(columns)}) VALUES ({', '.join(':' + column for column in columns)})")
    with engine.begin() as connection:
        connection.execute(statement, values)


def load_partition(settings, year, month=None, engine=None, environ=None, now=utc_now, log=print, pipeline_run_id=None):
    config = load_config(settings)
    started_at = now()
    run_label = resolve_pipeline_run_id(pipeline_run_id, environ, started_at)
    slice_path = partition_scope(settings, year, month)
    audit = {
        "pipeline_run_id": run_label,
        "curated_run_id": "unknown",
        "partition_path": slice_path,
        "load_year": int(year),
        "load_month": None if month is None else int(month),
        "rows_read": 0,
        "records_inserted": 0,
        "records_updated": 0,
        "records_quarantined": 0,
        "status": "failed",
        "error_message": None,
        "started_at_utc": started_at,
        "finished_at_utc": started_at,
    }
    own_engine = engine is None
    engine = engine or make_engine(settings, environ)
    failure = None
    report = None
    try:
        manifest, frame = read_partition_slice(settings, year, month)
        audit["curated_run_id"] = manifest["curated_run_id"]
        audit["rows_read"] = int(len(frame))
        log(f"Loading partition {slice_path} from curated run {manifest['curated_run_id']} ({len(frame)} rows, checksum verified)")
        report = load_frame(settings, frame, manifest["curated_run_id"], f"scope=partition/{slice_path}", engine=engine, environ=environ, now=now, log=log, pipeline_run_id=run_label)
        audit.update({
            "records_inserted": report["inserted"],
            "records_updated": report["updated"],
            "records_quarantined": report["rows_quarantined"],
            "status": "success",
        })
    except Exception as exc:
        failure = exc
        audit["error_message"] = f"{type(exc).__name__}: {exc}"[:2000]
    audit["finished_at_utc"] = now()
    try:
        write_audit(engine, config["audit_table"], audit)
        log(f"Audit row written to {config['audit_table']}: status={audit['status']}, inserted={audit['records_inserted']}, updated={audit['records_updated']}")
    except Exception as audit_exc:
        log(f"Audit row could not be written to {config['audit_table']}: {audit_exc}")
        if failure is None:
            failure = audit_exc
    finally:
        if own_engine:
            engine.dispose()
    if failure is not None:
        raise failure
    return report
