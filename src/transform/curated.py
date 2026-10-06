import hashlib
import json
import re
from pathlib import Path

import numpy as np
import pandas as pd

from src.extract.file_source import lane_root
from src.extract.raw_store import RawRun, utc_iso, utc_now
from src.transform.staging import (
    CONTRACTS_FILE,
    MEMBERS_FILE,
    REPORT_FILE,
    StagingError,
    latest_run_id as staging_latest_run_id,
    resolve_pipeline_run_id,
    sha256_file,
    write_atomically,
)

CURATED_CONTRACTS_FILE = "dpwh_contracts.parquet"
CURATED_MEMBERS_FILE = "contract_contractors.parquet"
CURATED_REGIONS_FILE = "psgc_regions.parquet"
CURATED_QUARANTINE_CONTRACTS = "contracts.parquet"
CURATED_QUARANTINE_MEMBERS = "contract_contractors.parquet"
CURATED_REPORT_FILE = "curated_report.json"
CURATED_LANE = "curated"

DELAY_STATUS = "On-Going"
PROGRESS_RANGE = (0.0, 100.0)
ROMAN_REGION = re.compile(r"\bREGION\s+([IVX]+(?:-[AB])?)\b")

RENAMED_COLUMNS = {
    "status": "status_name",
    "budget_php": "project_cost",
    "progress_pct": "physical_accomplishment",
}

CURATED_COLUMNS = (
    "contract_id", "contract_name", "description", "category", "infra_type", "status_name",
    "procurement_status_code", "region", "region_psgc_code", "region_psgc_name", "region_matches_psgc",
    "implementing_office", "infra_year", "program_name", "source_of_funds", "funding_instrument",
    "project_cost", "abc_php", "award_amount_php", "award_savings_php", "award_to_abc_pct",
    "physical_accomplishment", "start_date", "completion_date", "contract_effectivity_date",
    "expiry_date", "date_of_award", "latitude", "longitude", "contractor_count", "is_joint_venture",
    "bidder_count", "is_delayed", "delay_as_of_date", "warning_codes", "source_name", "source_revision",
    "raw_run_id", "pipeline_run_id", "_ingested_at_utc", "staged_at_utc", "processed_at_utc", "record_hash",
)

HASH_EXCLUDED = {
    "warning_codes", "raw_run_id", "pipeline_run_id", "_ingested_at_utc", "staged_at_utc",
    "processed_at_utc", "record_hash",
}
HASH_COLUMNS = tuple(column for column in CURATED_COLUMNS if column not in HASH_EXCLUDED)


class CuratedError(Exception):
    pass


def latest_run_id(root, what):
    try:
        return staging_latest_run_id(root)
    except StagingError as exc:
        raise CuratedError(f"no {what} runs found under {root}") from exc


def curated_config(settings):
    config = settings.raw.get("curated")
    if not config:
        raise CuratedError("config/settings.yml has no 'curated' section")
    return config


def normalise(text):
    return " ".join(str(text).upper().split())


def region_keys(name):
    text = normalise(name)
    keys = set()
    match = ROMAN_REGION.search(text)
    if match:
        keys.add(f"REGION {match.group(1)}")
    base = normalise(re.sub(r"\([^)]*\)", " ", text))
    if base:
        keys.add(base)
    for inner in re.findall(r"\(([^)]*)\)", text):
        if inner.strip():
            keys.add(normalise(inner))
    return keys


def load_psgc_regions(path, config):
    sheet = pd.read_excel(path, sheet_name=config["psgc_sheet"], dtype=str)
    needed = [config["psgc_code_column"], config["psgc_name_column"], config["psgc_level_column"]]
    missing = [column for column in needed if column not in sheet.columns]
    if missing:
        raise CuratedError(f"PSGC sheet '{config['psgc_sheet']}' is missing columns: {', '.join(missing)}")
    level = sheet[config["psgc_level_column"]].astype("string").str.strip()
    regions = sheet.loc[level == config["psgc_region_level"], [config["psgc_code_column"], config["psgc_name_column"]]].copy()
    regions.columns = ["psgc_code", "region_name"]
    regions["psgc_code"] = regions["psgc_code"].astype("string").str.strip()
    regions["region_name"] = regions["region_name"].astype("string").str.strip()
    if regions.empty:
        raise CuratedError("no region rows found in the PSGC sheet")
    return regions.sort_values("psgc_code").reset_index(drop=True)


def match_regions(labels, regions, aliases):
    region_key_sets = [(row.psgc_code, row.region_name, region_keys(row.region_name)) for row in regions.itertuples(index=False)]
    alias_lookup = {normalise(label): normalise(target) for label, target in (aliases or {}).items()}
    results = {}
    for label in labels:
        if label is None or pd.isna(label):
            continue
        keys = region_keys(label)
        candidates = [(code, name) for code, name, region_set in region_key_sets if keys & region_set]
        if not candidates and normalise(label) in alias_lookup:
            target = alias_lookup[normalise(label)]
            candidates = [(code, name) for code, name, _ in region_key_sets if target in normalise(name)]
        if len(candidates) == 1:
            results[label] = {"psgc_code": candidates[0][0], "psgc_name": candidates[0][1], "status": "matched"}
        elif candidates:
            results[label] = {"psgc_code": None, "psgc_name": None, "status": "ambiguous"}
        else:
            results[label] = {"psgc_code": None, "psgc_name": None, "status": "unmatched"}
    return results


def canonical(value):
    if value is None or value is pd.NA or value is pd.NaT:
        return ""
    if isinstance(value, float) and np.isnan(value):
        return ""
    if isinstance(value, (pd.Timestamp,)):
        return value.isoformat()
    if hasattr(value, "isoformat"):
        return value.isoformat()
    if isinstance(value, (bool, np.bool_)):
        return "true" if value else "false"
    if isinstance(value, (float, np.floating)):
        return repr(float(value))
    if isinstance(value, (int, np.integer)):
        return str(int(value))
    return str(value)


def record_hashes(frame):
    columns = [frame[column].astype("object").tolist() for column in HASH_COLUMNS]
    hashes = []
    for values in zip(*columns):
        payload = "\x1f".join(canonical(value) for value in values)
        hashes.append(hashlib.sha256(payload.encode("utf-8")).hexdigest())
    return pd.Series(hashes, index=frame.index, dtype="string")


def join_codes(left, right):
    joined = []
    for first, second in zip(left.astype("object"), right):
        parts = [part for part in (first if isinstance(first, str) else "").split("|") if part]
        parts.extend(second)
        joined.append("|".join(sorted(set(parts))) if parts else None)
    return pd.Series(joined, index=left.index, dtype="string")


def curate_frames(contracts, members, regions, context):
    df = contracts.copy().reset_index(drop=True)
    out = df.rename(columns=RENAMED_COLUMNS)

    matches = match_regions(out["region"].dropna().unique(), regions, context.get("region_aliases"))
    out["region_psgc_code"] = out["region"].map(lambda label: matches.get(label, {}).get("psgc_code")).astype("string")
    out["region_psgc_name"] = out["region"].map(lambda label: matches.get(label, {}).get("psgc_name")).astype("string")
    out["region_matches_psgc"] = out["region_psgc_code"].notna().astype("boolean")

    abc = out["abc_php"].astype("Float64")
    award = out["award_amount_php"].astype("Float64")
    both = abc.notna() & award.notna()
    out["award_savings_php"] = (abc - award).where(both)
    out["award_to_abc_pct"] = (award / abc * 100).where(both & (abc > 0)).round(4)

    as_of = pd.Timestamp(context["delay_as_of_date"]).date()
    expiry = pd.to_datetime(out["expiry_date"])
    progress = out["physical_accomplishment"].astype("Float64")
    delayed = (out["status_name"] == DELAY_STATUS) & expiry.notna() & (expiry < pd.Timestamp(as_of)) & (progress < 100)
    out["is_delayed"] = delayed.fillna(False).astype("boolean")
    out["delay_as_of_date"] = as_of

    progress_outside = progress.notna() & ((progress < PROGRESS_RANGE[0]) | (progress > PROGRESS_RANGE[1]))
    quarantine_mask = progress_outside.fillna(False).to_numpy()

    region_codes = [[] for _ in range(len(out))]
    for position, (label, matched) in enumerate(zip(out["region"], out["region_matches_psgc"])):
        if label is not None and not pd.isna(label) and not bool(matched):
            status = matches.get(label, {}).get("status", "unmatched")
            region_codes[position].append("W_REGION_AMBIGUOUS_IN_PSGC" if status == "ambiguous" else "W_REGION_NOT_IN_PSGC")
    out["warning_codes"] = join_codes(out["warning_codes"], region_codes)

    processed_at = pd.Timestamp(context["processed_at"]).tz_convert("UTC")
    out["pipeline_run_id"] = context["pipeline_run_id"]
    out["processed_at_utc"] = processed_at
    out["record_hash"] = record_hashes(out)

    curated = out.loc[~quarantine_mask, list(CURATED_COLUMNS)].sort_values("contract_id").reset_index(drop=True)
    rejected = out.loc[quarantine_mask].copy()
    rejected.insert(0, "error_codes", "Q_PROGRESS_OUT_OF_RANGE")
    rejected["quarantined_at_utc"] = processed_at
    rejected = rejected.reset_index(drop=True)

    member_frame = members.copy()
    kept_ids = set(curated["contract_id"])
    quarantined_ids = set(rejected["contract_id"])
    orphan = ~member_frame["contract_id"].isin(kept_ids | quarantined_ids)
    member_rejected = member_frame.loc[orphan].copy()
    member_rejected.insert(0, "error_codes", "Q_ORPHAN_CONTRACT_ID")
    member_rejected["quarantined_at_utc"] = processed_at
    member_curated = member_frame.loc[member_frame["contract_id"].isin(kept_ids)].copy()
    member_curated["pipeline_run_id"] = context["pipeline_run_id"]
    member_curated["processed_at_utc"] = processed_at
    member_curated = member_curated.sort_values(["contract_id", "member_position"]).reset_index(drop=True)

    region_table = pd.DataFrame(
        [
            {"region_label": label, "psgc_code": value["psgc_code"], "psgc_name": value["psgc_name"], "match_status": value["status"]}
            for label, value in sorted(matches.items())
        ],
        columns=["region_label", "psgc_code", "psgc_name", "match_status"],
    )
    label_counts = out["region"].value_counts(dropna=False)
    region_table["contracts"] = region_table["region_label"].map(label_counts).fillna(0).astype("int64")

    summary = {
        "rows_in": int(len(out)),
        "rows_curated": int(len(curated)),
        "rows_quarantined": int(len(rejected)),
        "member_rows_curated": int(len(member_curated)),
        "member_rows_quarantined": int(len(member_rejected)),
        "quarantine_counts": {"Q_PROGRESS_OUT_OF_RANGE": int(len(rejected)), "Q_ORPHAN_CONTRACT_ID": int(len(member_rejected))},
        "contracts_matching_psgc_region": int(curated["region_matches_psgc"].fillna(False).sum()),
        "contracts_not_matching_psgc_region": int((~curated["region_matches_psgc"].fillna(False)).sum()),
        "unmatched_region_labels": sorted(label for label, value in matches.items() if value["status"] != "matched"),
        "delayed_contracts": int(curated["is_delayed"].fillna(False).sum()),
        "delay_as_of_date": as_of.isoformat(),
    }
    if summary["rows_in"] != summary["rows_curated"] + summary["rows_quarantined"]:
        raise CuratedError("row accounting failed: curated plus quarantined rows do not equal staged rows")
    return curated, member_curated, rejected, member_rejected, region_table, summary


def latest_reference_file(settings, source_name):
    root = lane_root(settings.paths["raw"], source_name)
    run = RawRun.open_existing(root, latest_run_id(root, f"raw {source_name}"))
    run.verified_files()
    entries = [entry for entry in run.manifest_entries() if entry["file"].lower().endswith(".xlsx")]
    if len(entries) != 1:
        raise CuratedError(f"expected exactly one workbook in raw run {run.run_id} of {source_name}, found {len(entries)}")
    return run, entries[0]


def curated_contracts_path(settings, run_id=None):
    root = Path(settings.paths["curated"])
    run_id = run_id or latest_run_id(root, "curated")
    return root / f"run_id={run_id}" / CURATED_CONTRACTS_FILE


def run_curated(settings, run_id=None, rebuild=False, now=utc_now, log=print, pipeline_run_id=None, environ=None):
    config = curated_config(settings)
    primary = config["primary_source"]
    staging_root = lane_root(settings.paths["staging"], primary)
    run_id = run_id or latest_run_id(staging_root, "staging")
    staging_dir = staging_root / f"run_id={run_id}"
    staging_report_path = staging_dir / REPORT_FILE
    if not staging_report_path.exists():
        raise CuratedError(f"staging run {run_id} has no {REPORT_FILE}; run 'stage' first")

    curated_dir = Path(settings.paths["curated"]) / f"run_id={run_id}"
    quarantine_dir = Path(settings.paths["quarantine"]) / CURATED_LANE / f"run_id={run_id}"
    report_path = curated_dir / CURATED_REPORT_FILE
    if report_path.exists() and not rebuild:
        log(f"Unchanged: staging run {run_id} is already curated in {curated_dir}; use --rebuild to curate it again")
        return json.loads(report_path.read_text(encoding="utf-8")), "unchanged"

    staging_report = json.loads(staging_report_path.read_text(encoding="utf-8"))
    for name in (CONTRACTS_FILE, MEMBERS_FILE):
        recorded = staging_report["outputs"][f"staging/{name}"]["sha256"]
        if sha256_file(staging_dir / name) != recorded:
            raise CuratedError(f"checksum mismatch for staging file {name}; staging output was modified")

    reference_run, reference_entry = latest_reference_file(settings, config["reference_source"])
    regions = load_psgc_regions(reference_run.directory / reference_entry["file"], config)
    log(f"Curating staging run {run_id} with PSGC reference run {reference_run.run_id} ({len(regions)} regions)")

    contracts = pd.read_parquet(staging_dir / CONTRACTS_FILE)
    members = pd.read_parquet(staging_dir / MEMBERS_FILE)
    revision_date = settings.raw["file_sources"][primary].get("revision_date_utc")
    if not revision_date:
        raise CuratedError(f"file source '{primary}' needs revision_date_utc for the delay as-of date")

    processed_at = now()
    context = {
        "pipeline_run_id": resolve_pipeline_run_id(pipeline_run_id, environ, processed_at),
        "processed_at": processed_at,
        "delay_as_of_date": revision_date,
        "region_aliases": config.get("region_aliases"),
    }
    curated, member_curated, rejected, member_rejected, region_table, summary = curate_frames(contracts, members, regions, context)

    report = {
        "status": "complete",
        "staging_run_id": run_id,
        "reference_source": config["reference_source"],
        "reference_run_id": reference_run.run_id,
        "reference_sha256": reference_entry["sha256"],
        "pipeline_run_id": context["pipeline_run_id"],
        "processed_at_utc": utc_iso(processed_at),
        "record_hash_columns": list(HASH_COLUMNS),
        **summary,
        "outputs": {},
    }

    def write_quarantine(directory):
        for name, frame in ((CURATED_QUARANTINE_CONTRACTS, rejected), (CURATED_QUARANTINE_MEMBERS, member_rejected)):
            path = directory / name
            frame.to_parquet(path, index=False)
            report["outputs"][f"quarantine/curated/{name}"] = {"rows": int(len(frame)), "sha256": sha256_file(path)}

    def write_curated(directory):
        for name, frame in ((CURATED_CONTRACTS_FILE, curated), (CURATED_MEMBERS_FILE, member_curated), (CURATED_REGIONS_FILE, region_table)):
            path = directory / name
            frame.to_parquet(path, index=False)
            report["outputs"][f"curated/{name}"] = {"rows": int(len(frame)), "sha256": sha256_file(path)}
        (directory / CURATED_REPORT_FILE).write_text(json.dumps(report, indent=2, sort_keys=True), encoding="utf-8")

    write_atomically(quarantine_dir, write_quarantine)
    write_atomically(curated_dir, write_curated)

    log(f"Rows in: {report['rows_in']}, curated: {report['rows_curated']}, quarantined: {report['rows_quarantined']}")
    log(f"Regions matched to PSGC: {report['contracts_matching_psgc_region']} contracts; unmatched labels: {report['unmatched_region_labels'] or 'none'}")
    log(f"Delayed contracts as of {report['delay_as_of_date']}: {report['delayed_contracts']}")
    log(f"Curated to {curated_dir}")
    return report, "curated"

