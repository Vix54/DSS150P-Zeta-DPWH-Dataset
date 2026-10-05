import hashlib
import json
import os
import shutil
from pathlib import Path

import numpy as np
import pandas as pd

from src.extract.file_source import lane_root
from src.extract.raw_store import RawRun, utc_iso, utc_now
from src.transform.cleaning import (
    clean_text,
    nested_to_json,
    parse_bool,
    parse_datetime,
    parse_integer,
    parse_number,
    same_values,
    to_dates,
)
from src.transform.contractors import parse_contractor, rebuild_winner_names

SOURCE_COLUMNS = (
    "contractId", "description", "category", "status", "budget", "amountPaid", "progress", "region",
    "province", "infraType", "latitude", "longitude", "verified", "infraType_1", "contractor",
    "startDate", "completionDate", "infraYear", "contractEffectivityDate", "expiryDate", "nysReason",
    "programName", "sourceOfFunds", "isVerifiedByDpwh", "isVerifiedByPublic", "isLive", "livestreamUrl",
    "livestreamVideoId", "livestreamDetectedAt", "components", "winnerNames", "bidders", "contractName",
    "abc", "status_1", "fundingInstrument", "advertisementDate", "bidSubmissionDeadline", "dateOfAward",
    "awardAmount", "advertisement", "contractAgreement", "noticeOfAward", "noticeToProceed",
    "programOfWork", "engineeringDesign", "latitude_1", "longitude_1", "coordinates", "totalImages",
    "latestImageDate", "hasImages",
)

TEXT_COLUMNS = {
    "contractName": "contract_name",
    "description": "description",
    "category": "category",
    "infraType": "infra_type",
    "status": "status",
    "status_1": "procurement_status_code",
    "nysReason": "nys_reason",
    "region": "region",
    "province": "implementing_office",
    "programName": "program_name",
    "sourceOfFunds": "source_of_funds",
    "fundingInstrument": "funding_instrument",
    "advertisement": "advertisement_url",
    "contractAgreement": "contract_agreement_url",
    "noticeOfAward": "notice_of_award_url",
    "noticeToProceed": "notice_to_proceed_url",
    "programOfWork": "program_of_work_url",
    "engineeringDesign": "engineering_design_url",
    "livestreamUrl": "livestream_url",
    "livestreamVideoId": "livestream_video_id",
}
NUMBER_COLUMNS = {
    "budget": "budget_php",
    "abc": "abc_php",
    "awardAmount": "award_amount_php",
    "amountPaid": "amount_paid_php",
    "progress": "progress_pct",
    "latitude": "latitude",
    "longitude": "longitude",
}
INTEGER_COLUMNS = {"infraYear": "infra_year", "totalImages": "total_images"}
DATE_COLUMNS = {
    "startDate": "start_date",
    "completionDate": "completion_date",
    "contractEffectivityDate": "contract_effectivity_date",
    "expiryDate": "expiry_date",
}
TIMESTAMP_COLUMNS = {
    "advertisementDate": "advertisement_date",
    "bidSubmissionDeadline": "bid_submission_deadline",
    "dateOfAward": "date_of_award",
    "latestImageDate": "latest_image_at",
    "livestreamDetectedAt": "livestream_detected_at",
}
BOOL_COLUMNS = {
    "verified": "is_verified",
    "isVerifiedByDpwh": "is_verified_by_dpwh",
    "isVerifiedByPublic": "is_verified_by_public",
    "isLive": "is_live",
    "hasImages": "has_images",
}
NESTED_COLUMNS = {
    "components": ("components_json", "component_count"),
    "bidders": ("bidders_json", "bidder_count"),
    "coordinates": ("coordinates_json", "coordinate_count"),
}
DUPLICATE_COLUMNS = {"infraType_1": "infraType", "latitude_1": "latitude", "longitude_1": "longitude"}
AMOUNT_COLUMNS = ("budget_php", "abc_php", "award_amount_php", "amount_paid_php")

STATUS_VALUES = ("Completed", "On-Going", "For Procurement", "Terminated", "Not Yet Started")
NO_CONTRACTOR_STATUS = "For Procurement"
LATITUDE_RANGE = (4.0, 21.5)
LONGITUDE_RANGE = (116.0, 127.0)

CONTRACT_COLUMNS = (
    "contract_id", "contract_name", "description", "category", "infra_type", "status",
    "procurement_status_code", "nys_reason", "region", "implementing_office", "infra_year",
    "program_name", "source_of_funds", "funding_instrument", "budget_php", "abc_php",
    "award_amount_php", "amount_paid_php", "progress_pct", "start_date", "completion_date",
    "contract_effectivity_date", "expiry_date", "advertisement_date", "bid_submission_deadline",
    "date_of_award", "latitude", "longitude", "contractor_raw", "winner_names_raw",
    "contractor_count", "is_joint_venture", "bidder_count", "component_count", "coordinate_count",
    "bidders_json", "components_json", "coordinates_json", "advertisement_url",
    "contract_agreement_url", "notice_of_award_url", "notice_to_proceed_url", "program_of_work_url",
    "engineering_design_url", "is_verified", "is_verified_by_dpwh", "is_verified_by_public",
    "total_images", "has_images", "latest_image_at", "is_live", "livestream_url",
    "livestream_video_id", "livestream_detected_at", "warning_codes", "source_name",
    "source_revision", "raw_run_id", "_ingested_at_utc", "staged_at_utc",
)
MEMBER_COLUMNS = (
    "contract_id", "member_position", "member_raw", "display_name", "contractor_name", "former_name",
    "contractor_source_id", "has_revoked_marker", "name_truncated", "source_name", "raw_run_id",
    "staged_at_utc",
)

CONTRACTS_FILE = "contracts.parquet"
MEMBERS_FILE = "contract_contractors.parquet"
QUARANTINE_FILE = "contracts.parquet"
REPORT_FILE = "staging_report.json"


class StagingError(Exception):
    pass


class Issues:
    def __init__(self, index):
        self.index = index
        self.masks = {}

    def add(self, code, mask):
        mask = pd.Series(mask, index=self.index).fillna(False).astype(bool)
        if mask.any():
            self.masks[code] = self.masks.get(code, False) | mask

    def any(self):
        combined = pd.Series(False, index=self.index)
        for mask in self.masks.values():
            combined = combined | mask
        return combined

    def codes(self):
        rows = [[] for _ in range(len(self.index))]
        for code in sorted(self.masks):
            for position in np.flatnonzero(self.masks[code].to_numpy()):
                rows[position].append(code)
        return pd.Series(["|".join(row) if row else None for row in rows], index=self.index, dtype="string")

    def counts(self, rows=None):
        result = {}
        for code in sorted(self.masks):
            mask = self.masks[code] if rows is None else self.masks[code] & rows
            total = int(mask.sum())
            if total:
                result[code] = total
        return result


def check_schema(df):
    missing = [column for column in SOURCE_COLUMNS if column not in df.columns]
    extra = [column for column in df.columns if column not in SOURCE_COLUMNS]
    if missing or extra:
        raise StagingError(
            f"source schema differs from the data contract; missing: {missing or 'none'}, unexpected: {extra or 'none'}; "
            "update docs/data_contract.md and the staging mappings before staging this file"
        )


def stage_contracts(df_raw, context):
    check_schema(df_raw)
    df = df_raw.copy().reset_index(drop=True)
    out = pd.DataFrame(index=df.index)
    quarantine = Issues(df.index)
    warnings = Issues(df.index)

    out["contract_id"] = clean_text(df["contractId"])
    quarantine.add("Q_MISSING_CONTRACT_ID", out["contract_id"].isna())
    quarantine.add("Q_DUPLICATE_CONTRACT_ID", out["contract_id"].notna() & out["contract_id"].duplicated(keep=False))

    for source, target in TEXT_COLUMNS.items():
        out[target] = clean_text(df[source])

    for source, target in NUMBER_COLUMNS.items():
        out[target], bad = parse_number(df[source])
        quarantine.add(f"Q_BAD_NUMBER:{source}", bad)

    for source, target in INTEGER_COLUMNS.items():
        out[target], bad = parse_integer(df[source])
        quarantine.add(f"Q_BAD_INTEGER:{source}", bad)

    for source, target in DATE_COLUMNS.items():
        parsed, bad = parse_datetime(df[source])
        out[target] = to_dates(parsed)
        quarantine.add(f"Q_BAD_DATE:{source}", bad)

    for source, target in TIMESTAMP_COLUMNS.items():
        out[target], bad = parse_datetime(df[source])
        quarantine.add(f"Q_BAD_DATE:{source}", bad)

    for source, target in BOOL_COLUMNS.items():
        out[target], bad = parse_bool(df[source])
        warnings.add(f"W_BAD_BOOLEAN:{source}", bad)

    for source, (json_target, count_target) in NESTED_COLUMNS.items():
        out[json_target], out[count_target] = nested_to_json(df[source])

    for duplicate, original in DUPLICATE_COLUMNS.items():
        warnings.add(f"W_DUPLICATE_COLUMN_MISMATCH:{duplicate}", ~same_values(df[duplicate], df[original]))

    warnings.add("W_STATUS_MISSING", out["status"].isna())
    warnings.add("W_STATUS_UNKNOWN", out["status"].notna() & ~out["status"].isin(STATUS_VALUES).fillna(False))
    warnings.add("W_PROGRESS_RANGE", (out["progress_pct"] < 0) | (out["progress_pct"] > 100))
    for column in AMOUNT_COLUMNS:
        warnings.add(f"W_NEGATIVE_AMOUNT:{column}", out[column] < 0)

    has_latitude = out["latitude"].notna()
    has_longitude = out["longitude"].notna()
    warnings.add("W_COORDINATES_PARTIAL", has_latitude != has_longitude)
    outside = (
        (out["latitude"] < LATITUDE_RANGE[0]) | (out["latitude"] > LATITUDE_RANGE[1])
        | (out["longitude"] < LONGITUDE_RANGE[0]) | (out["longitude"] > LONGITUDE_RANGE[1])
    )
    warnings.add("W_COORDINATES_OUTSIDE_PH", has_latitude & has_longitude & outside)

    start = pd.to_datetime(out["start_date"])
    completion = pd.to_datetime(out["completion_date"])
    effectivity = pd.to_datetime(out["contract_effectivity_date"])
    expiry = pd.to_datetime(out["expiry_date"])
    warnings.add("W_DATE_ORDER:completion_date", completion < start)
    warnings.add("W_DATE_ORDER:expiry_date", expiry < effectivity)

    out["contractor_raw"] = clean_text(df["contractor"])
    out["winner_names_raw"] = clean_text(df["winnerNames"])
    members_by_row = [parse_contractor(value) for value in out["contractor_raw"].astype("object")]
    out["contractor_count"] = pd.Series([len(members) for members in members_by_row], index=df.index, dtype="Int64")
    out["is_joint_venture"] = (out["contractor_count"] > 1).astype("boolean")

    has_contractor = out["contractor_raw"].notna()
    has_winner = out["winner_names_raw"].notna()
    rebuilt = pd.Series(
        [rebuild_winner_names(members) if members else None for members in members_by_row],
        index=df.index,
        dtype="string",
    )
    warnings.add("W_CONTRACTOR_MISSING", ~has_contractor & (out["status"] != NO_CONTRACTOR_STATUS).fillna(True))
    warnings.add("W_WINNER_NAMES_WITHOUT_CONTRACTOR", ~has_contractor & has_winner)
    warnings.add("W_WINNER_NAMES_MISSING", has_contractor & ~has_winner)
    warnings.add("W_WINNER_NAMES_MISMATCH", has_contractor & has_winner & (rebuilt != out["winner_names_raw"]).fillna(True))
    warnings.add(
        "W_CONTRACTOR_ID_MISSING",
        [any(member["contractor_source_id"] is None for member in members) for members in members_by_row],
    )
    warnings.add(
        "W_CONTRACTOR_NAME_TRUNCATED",
        [any(member["name_truncated"] for member in members) for members in members_by_row],
    )

    quarantined = quarantine.any()
    kept = ~quarantined
    staged_at = pd.Timestamp(context["staged_at"]).tz_convert("UTC")
    ingested_at = pd.Timestamp(context["ingested_at"]).tz_convert("UTC") if context.get("ingested_at") else pd.NaT

    out["warning_codes"] = warnings.codes()
    out["source_name"] = context["source_name"]
    out["source_revision"] = context.get("source_revision")
    out["raw_run_id"] = context["raw_run_id"]
    out["_ingested_at_utc"] = ingested_at
    out["staged_at_utc"] = staged_at

    contracts = out.loc[kept, list(CONTRACT_COLUMNS)].sort_values("contract_id").reset_index(drop=True)

    member_rows = []
    for position in np.flatnonzero(kept.to_numpy()):
        contract_id = out.at[position, "contract_id"]
        for number, member in enumerate(members_by_row[position], start=1):
            member_rows.append({"contract_id": contract_id, "member_position": number, **member})
    members = pd.DataFrame(member_rows, columns=[column for column in MEMBER_COLUMNS if column not in ("source_name", "raw_run_id", "staged_at_utc")])
    members["member_position"] = members["member_position"].astype("Int64")
    members["contractor_source_id"] = members["contractor_source_id"].astype("Int64")
    members["has_revoked_marker"] = members["has_revoked_marker"].astype("boolean")
    members["name_truncated"] = members["name_truncated"].astype("boolean")
    members["source_name"] = context["source_name"]
    members["raw_run_id"] = context["raw_run_id"]
    members["staged_at_utc"] = staged_at
    members = members.loc[:, list(MEMBER_COLUMNS)].sort_values(["contract_id", "member_position"]).reset_index(drop=True)

    rejected = df_raw.reset_index(drop=True).loc[quarantined].copy()
    rejected.insert(0, "error_codes", quarantine.codes().loc[quarantined])
    rejected["source_name"] = context["source_name"]
    rejected["raw_run_id"] = context["raw_run_id"]
    rejected["quarantined_at_utc"] = staged_at
    rejected = rejected.reset_index(drop=True)

    summary = {
        "rows_in": int(len(df)),
        "rows_staged": int(kept.sum()),
        "rows_quarantined": int(quarantined.sum()),
        "contractor_member_rows": int(len(members)),
        "joint_ventures": int(contracts["is_joint_venture"].fillna(False).sum()),
        "members_with_revoked_marker": int(members["has_revoked_marker"].fillna(False).sum()),
        "quarantine_counts": quarantine.counts(),
        "warning_counts_staged": warnings.counts(kept),
        "status_counts_staged": {str(key): int(value) for key, value in contracts["status"].value_counts(dropna=False).items()},
    }
    if summary["rows_in"] != summary["rows_staged"] + summary["rows_quarantined"]:
        raise StagingError("row accounting failed: staged plus quarantined rows do not equal input rows")
    return contracts, members, rejected, summary


def sha256_file(path):
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for block in iter(lambda: handle.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def latest_run_id(root):
    run_ids = sorted(directory.name.split("=", 1)[1] for directory in Path(root).glob("run_id=*") if directory.is_dir())
    if not run_ids:
        raise StagingError(f"no raw runs found under {root}; run extract-file first")
    return run_ids[-1]


def raw_parquet_entry(run):
    entries = [entry for entry in run.manifest_entries() if entry["file"].endswith(".parquet")]
    if len(entries) != 1:
        raise StagingError(f"expected exactly one Parquet file in raw run {run.run_id}, found {len(entries)}")
    return entries[0]


def write_atomically(target, write):
    target = Path(target)
    temporary = target.with_name(target.name + ".part")
    if temporary.exists():
        shutil.rmtree(temporary)
    temporary.mkdir(parents=True)
    write(temporary)
    if target.exists():
        shutil.rmtree(target)
    os.replace(temporary, target)


def run_staging(settings, source_name="bettergov_hf", run_id=None, rebuild=False, now=utc_now, log=print):
    raw_root = lane_root(settings.paths["raw"], source_name)
    run_id = run_id or latest_run_id(raw_root)
    run = RawRun.open_existing(raw_root, run_id)
    staging_dir = lane_root(settings.paths["staging"], source_name) / f"run_id={run_id}"
    quarantine_dir = lane_root(settings.paths["quarantine"], source_name) / f"run_id={run_id}"

    report_path = staging_dir / REPORT_FILE
    if report_path.exists() and not rebuild:
        log(f"Unchanged: raw run {run_id} is already staged in {staging_dir}; use --rebuild to stage it again")
        return json.loads(report_path.read_text(encoding="utf-8")), "unchanged"

    run.verified_files()
    entry = raw_parquet_entry(run)
    metadata = run.read_metadata()
    log(f"Staging {entry['file']} from raw run {run_id} (checksum verified)")
    df_raw = pd.read_parquet(run.directory / entry["file"])

    staged_at = now()
    context = {
        "source_name": source_name,
        "source_revision": metadata.get("revision"),
        "raw_run_id": run_id,
        "ingested_at": metadata.get("ingested_at_utc"),
        "staged_at": staged_at,
    }
    contracts, members, rejected, summary = stage_contracts(df_raw, context)

    report = {
        "status": "complete",
        "source_name": source_name,
        "source_revision": metadata.get("revision"),
        "raw_run_id": run_id,
        "raw_file": entry["file"],
        "raw_sha256": entry["sha256"],
        "staged_at_utc": utc_iso(staged_at),
        **summary,
        "outputs": {},
    }

    def write_quarantine(directory):
        path = directory / QUARANTINE_FILE
        rejected.to_parquet(path, index=False)
        report["outputs"][f"quarantine/{QUARANTINE_FILE}"] = {"rows": int(len(rejected)), "sha256": sha256_file(path)}

    def write_staging(directory):
        for name, frame in ((CONTRACTS_FILE, contracts), (MEMBERS_FILE, members)):
            path = directory / name
            frame.to_parquet(path, index=False)
            report["outputs"][f"staging/{name}"] = {"rows": int(len(frame)), "sha256": sha256_file(path)}
        (directory / REPORT_FILE).write_text(json.dumps(report, indent=2, sort_keys=True), encoding="utf-8")

    write_atomically(quarantine_dir, write_quarantine)
    write_atomically(staging_dir, write_staging)

    log(f"Rows in: {report['rows_in']}, staged: {report['rows_staged']}, quarantined: {report['rows_quarantined']}")
    log(f"Contractor member rows: {report['contractor_member_rows']} (joint ventures: {report['joint_ventures']})")
    for code, count in report["quarantine_counts"].items():
        log(f"  quarantine {code}: {count}")
    for code, count in report["warning_counts_staged"].items():
        log(f"  warning {code}: {count}")
    log(f"Staged to {staging_dir}")
    return report, "staged"
