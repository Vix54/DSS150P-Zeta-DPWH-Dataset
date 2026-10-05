import datetime as dt
import io
import json
from datetime import datetime, timezone

import pandas as pd
import pytest

from src.cli import build_parser
from src.config import PROJECT_ROOT, Settings
from src.extract.file_source import lane_root
from src.extract.raw_store import RawRun, RawStoreError
from src.transform import staging
from src.transform.contractors import collation_key, parse_contractor, parse_member, rebuild_winner_names

FIXED = datetime(2026, 10, 5, 8, 0, 0, tzinfo=timezone.utc)


def fixed_now():
    return FIXED


def row(**overrides):
    base = {
        "contractId": "22DJ0023",
        "description": "ROAD CONCRETING",
        "category": "Roads",
        "status": "Completed",
        "budget": 4950000.0,
        "amountPaid": 0,
        "progress": 100.0,
        "region": "Region IV-A",
        "province": "Quezon 2nd DEO",
        "infraType": "Roads",
        "latitude": 13.945833,
        "longitude": 121.35107,
        "verified": False,
        "infraType_1": "Roads",
        "contractor": "739 BUILDERS (31174)",
        "startDate": "2022-02-24",
        "completionDate": "2022-06-23",
        "infraYear": "2022",
        "contractEffectivityDate": "2022-02-24",
        "expiryDate": "2022-06-23",
        "nysReason": None,
        "programName": "Regular Infra",
        "sourceOfFunds": "Regular Infra - GAA 2022",
        "isVerifiedByDpwh": False,
        "isVerifiedByPublic": False,
        "isLive": False,
        "livestreamUrl": None,
        "livestreamVideoId": None,
        "livestreamDetectedAt": None,
        "components": [{"componentId": "P1-CW1", "description": "ROAD"}],
        "winnerNames": "739 BUILDERS",
        "bidders": [{"name": "739 BUILDERS"}],
        "contractName": "ROAD CONCRETING",
        "abc": "4950000.00",
        "status_1": "F",
        "fundingInstrument": "GOP-GOVERNMENT OF THE PHILIPPINES",
        "advertisementDate": "2021-10-27 00:00:00.0000000",
        "bidSubmissionDeadline": "2021-11-16 00:00:00.0000000",
        "dateOfAward": "2022-02-11 00:00:00.0000000",
        "awardAmount": "4781700",
        "advertisement": "https://docs.example.test/advertisement/1",
        "contractAgreement": "https://docs.example.test/contract_agreement/1",
        "noticeOfAward": "https://docs.example.test/notice_of_award/1",
        "noticeToProceed": "https://docs.example.test/notice_to_proceed/1",
        "programOfWork": "",
        "engineeringDesign": "",
        "latitude_1": 13.945833,
        "longitude_1": 121.35107,
        "coordinates": [{"componentId": "P1-CW1", "description": "ROAD"}],
        "totalImages": 1,
        "latestImageDate": "2022-06-30 02:37:06.220999",
        "hasImages": True,
    }
    base.update(overrides)
    return base


def fixture_frame():
    rows = [
        row(),
        row(
            contractId="JV0001",
            contractor="A.M. ORETA & CO., INC. (108) / ALLENCON DEVELOPMENT CORPORATION (12302)",
            winnerNames="ALLENCON DEVELOPMENT CORPORATION, A.M. ORETA & CO., INC.",
        ),
        row(
            contractId="TR0001",
            contractor="DSB CONSTRUCTION  & SUPPLY, INC. (FORMERLY: DSB CO ([REVOKED] 16126)",
            winnerNames="DSB CONSTRUCTION  & SUPPLY, INC. (FORMERLY: DSB CO",
        ),
        row(contractId="FP0001", status="For Procurement", contractor=None, winnerNames="", progress=0.0),
        row(contractId="NC0001", contractor=None, winnerNames=""),
        row(contractId="NW0001", winnerNames=""),
        row(contractId="BADABC", abc="N/A"),
        row(contractId=""),
        row(contractId="DUP0001"),
        row(contractId="DUP0001"),
        row(contractId="BADDATE", startDate="31/02/2022"),
        row(contractId="ZERO01", latitude=0.0, longitude=0.0, latitude_1=0.0, longitude_1=1.0),
        row(contractId="ORDER01", startDate="2022-06-23", completionDate="2022-02-24"),
        row(contractId="NOID01", contractor="SOME BUILDER", winnerNames="SOME BUILDER"),
        row(
            contractId="ALTDATE",
            status="For Procurement",
            contractor=None,
            winnerNames="",
            advertisementDate="01/01/1900 12:00:00 AM",
            bidSubmissionDeadline="01/01/1900 12:00:00 AM",
            dateOfAward="12/09/2025 12:00:00 AM",
        ),
        row(contractId="ISOPH01", startDate="1900-01-01"),
        row(
            contractId="DUPMEM",
            contractor="PANAAD CONSTRUCTION (37345) / PANAAD CONSTRUCTION (37345) / PANAAD CONSTRUCTION (37345)",
            winnerNames="PANAAD CONSTRUCTION",
        ),
    ]
    return pd.DataFrame(rows, columns=list(staging.SOURCE_COLUMNS))


def make_settings(tmp_path):
    return Settings(
        raw={},
        paths={"raw": tmp_path / "raw", "staging": tmp_path / "staging", "quarantine": tmp_path / "quarantine"},
    )


def make_raw_run(tmp_path, frame=None):
    frame = fixture_frame() if frame is None else frame
    buffer = io.BytesIO()
    frame.to_parquet(buffer, index=False)
    run = RawRun.create(lane_root(tmp_path / "raw", "bettergov_hf"), now=fixed_now)
    run.write("file.parquet", buffer.getvalue(), "https://example.test/file.parquet", None, FIXED)
    run.write_metadata({"revision": "abc123", "ingested_at_utc": "2026-10-05T07:00:00Z"})
    return run


def stage(tmp_path, **kwargs):
    return staging.run_staging(make_settings(tmp_path), now=fixed_now, log=lambda message: None, **kwargs)


def outputs(tmp_path, run_id):
    staged = lane_root(tmp_path / "staging", "bettergov_hf") / f"run_id={run_id}"
    quarantined = lane_root(tmp_path / "quarantine", "bettergov_hf") / f"run_id={run_id}"
    return (
        pd.read_parquet(staged / staging.CONTRACTS_FILE),
        pd.read_parquet(staged / staging.MEMBERS_FILE),
        pd.read_parquet(quarantined / staging.QUARANTINE_FILE),
    )


def warnings_for(contracts, contract_id):
    value = contracts.loc[contracts["contract_id"] == contract_id, "warning_codes"].iloc[0]
    return set() if pd.isna(value) else set(value.split("|"))


def test_member_parser_handles_revoked_former_and_truncated_names():
    member = parse_member("DSB CONSTRUCTION  & SUPPLY, INC. (FORMERLY: DSB CO ([REVOKED] 16126)")
    assert member["contractor_source_id"] == 16126
    assert member["has_revoked_marker"] is True
    assert member["contractor_name"] == "DSB CONSTRUCTION  & SUPPLY, INC."
    assert member["former_name"] == "DSB CO"
    assert member["name_truncated"] is True
    complete = parse_member("CYJE FIRST BUILDERS CONSTRUCTION CORP. (FORMERLY: FIRST BUILDERS AND DEVELOPERS) (31124)")
    assert complete["former_name"] == "FIRST BUILDERS AND DEVELOPERS"
    assert complete["name_truncated"] is False
    assert parse_member("V.R. PATRON BUILDERS (PREVIOUSL (13487)")["contractor_name"] == "V.R. PATRON BUILDERS"


def test_winner_names_rebuild_ignores_punctuation_when_sorting():
    members = parse_contractor("RCDG CONSTRUCTION CORPORATION (5063) / R. ROSE CONSTRUCTION SERVICES (51340)")
    assert rebuild_winner_names(members) == "RCDG CONSTRUCTION CORPORATION, R. ROSE CONSTRUCTION SERVICES"
    assert collation_key("A.M. ORETA & CO.") == "AMORETACO"
    assert parse_contractor(None) == []


def test_every_row_is_staged_or_quarantined(tmp_path):
    run = make_raw_run(tmp_path)
    report, status = stage(tmp_path)
    assert status == "staged"
    assert report["rows_in"] == 17
    assert report["rows_quarantined"] == 5
    assert report["rows_staged"] == 12
    contracts, _, rejected = outputs(tmp_path, run.run_id)
    assert len(contracts) == 12
    assert len(rejected) == 5


def test_structural_failures_go_to_quarantine_with_codes(tmp_path):
    run = make_raw_run(tmp_path)
    report, _ = stage(tmp_path)
    _, _, rejected = outputs(tmp_path, run.run_id)
    codes = dict(zip(rejected["contractId"], rejected["error_codes"]))
    assert codes["BADABC"] == "Q_BAD_NUMBER:abc"
    assert codes["BADDATE"] == "Q_BAD_DATE:startDate"
    assert codes["DUP0001"] == "Q_DUPLICATE_CONTRACT_ID"
    assert codes[""] == "Q_MISSING_CONTRACT_ID"
    assert report["quarantine_counts"]["Q_DUPLICATE_CONTRACT_ID"] == 2
    assert rejected["abc"].tolist().count("N/A") == 1


def test_odd_but_usable_rows_stay_with_warnings(tmp_path):
    run = make_raw_run(tmp_path)
    stage(tmp_path)
    contracts, _, _ = outputs(tmp_path, run.run_id)
    assert warnings_for(contracts, "22DJ0023") == set()
    assert warnings_for(contracts, "JV0001") == set()
    assert warnings_for(contracts, "FP0001") == set()
    assert warnings_for(contracts, "NC0001") == {"W_CONTRACTOR_MISSING"}
    assert warnings_for(contracts, "NW0001") == {"W_WINNER_NAMES_MISSING"}
    assert warnings_for(contracts, "TR0001") == {"W_CONTRACTOR_NAME_TRUNCATED"}
    assert warnings_for(contracts, "ZERO01") == {"W_COORDINATES_OUTSIDE_PH", "W_DUPLICATE_COLUMN_MISMATCH:longitude_1"}
    assert warnings_for(contracts, "ORDER01") == {"W_DATE_ORDER:completion_date"}
    assert warnings_for(contracts, "NOID01") == {"W_CONTRACTOR_ID_MISSING"}
    assert warnings_for(contracts, "ISOPH01") == {"W_PLACEHOLDER_DATE:startDate"}


def test_alternate_format_and_placeholder_dates_are_staged(tmp_path):
    run = make_raw_run(tmp_path)
    stage(tmp_path)
    contracts, _, rejected = outputs(tmp_path, run.run_id)
    assert "ALTDATE" not in set(rejected["contractId"])
    alternate = contracts.loc[contracts["contract_id"] == "ALTDATE"].iloc[0]
    assert alternate["date_of_award"] == pd.Timestamp("2025-12-09")
    assert pd.isna(alternate["advertisement_date"])
    assert pd.isna(alternate["bid_submission_deadline"])
    assert warnings_for(contracts, "ALTDATE") == {
        "W_DATE_FORMAT_ALT:advertisementDate",
        "W_DATE_FORMAT_ALT:bidSubmissionDeadline",
        "W_DATE_FORMAT_ALT:dateOfAward",
        "W_PLACEHOLDER_DATE:advertisementDate",
        "W_PLACEHOLDER_DATE:bidSubmissionDeadline",
    }
    assert pd.isna(contracts.loc[contracts["contract_id"] == "ISOPH01", "start_date"].iloc[0])


def test_repeated_members_are_counted_once(tmp_path):
    run = make_raw_run(tmp_path)
    stage(tmp_path)
    contracts, members, _ = outputs(tmp_path, run.run_id)
    repeated = contracts.loc[contracts["contract_id"] == "DUPMEM"].iloc[0]
    assert repeated["contractor_count"] == 1
    assert bool(repeated["is_joint_venture"]) is False
    assert warnings_for(contracts, "DUPMEM") == {"W_CONTRACTOR_DUPLICATE_MEMBER"}
    assert members.loc[members["contract_id"] == "DUPMEM", "member_position"].tolist() == [1]


def test_types_follow_the_data_contract(tmp_path):
    run = make_raw_run(tmp_path)
    stage(tmp_path)
    contracts, _, _ = outputs(tmp_path, run.run_id)
    first = contracts.loc[contracts["contract_id"] == "22DJ0023"].iloc[0]
    assert first["abc_php"] == 4950000.0
    assert first["award_amount_php"] == 4781700.0
    assert first["infra_year"] == 2022
    assert first["start_date"] == dt.date(2022, 2, 24)
    assert first["advertisement_date"] == pd.Timestamp("2021-10-27")
    assert first["latest_image_at"] == pd.Timestamp("2022-06-30 02:37:06.220999")
    assert bool(first["is_verified"]) is False
    assert first["implementing_office"] == "Quezon 2nd DEO"
    assert pd.isna(first["program_of_work_url"])
    assert json.loads(first["components_json"]) == [{"componentId": "P1-CW1", "description": "ROAD"}]
    assert first["component_count"] == 1
    assert first["source_revision"] == "abc123"
    assert str(first["staged_at_utc"]) == "2026-10-05 08:00:00+00:00"
    assert list(contracts.columns) == list(staging.CONTRACT_COLUMNS)


def test_contractor_members_table(tmp_path):
    run = make_raw_run(tmp_path)
    report, _ = stage(tmp_path)
    contracts, members, _ = outputs(tmp_path, run.run_id)
    venture = members.loc[members["contract_id"] == "JV0001"]
    assert venture["member_position"].tolist() == [1, 2]
    assert venture["contractor_source_id"].tolist() == [108, 12302]
    assert bool(contracts.loc[contracts["contract_id"] == "JV0001", "is_joint_venture"].iloc[0]) is True
    revoked = members.loc[members["contract_id"] == "TR0001"].iloc[0]
    assert bool(revoked["has_revoked_marker"]) is True
    assert revoked["former_name"] == "DSB CO"
    assert report["joint_ventures"] == 1
    assert report["members_with_revoked_marker"] == 1
    assert "FP0001" not in set(members["contract_id"])
    assert list(members.columns) == list(staging.MEMBER_COLUMNS)


def test_rerun_is_unchanged_until_rebuild(tmp_path):
    make_raw_run(tmp_path)
    stage(tmp_path)
    _, status = stage(tmp_path)
    assert status == "unchanged"
    _, status = stage(tmp_path, rebuild=True)
    assert status == "staged"


def test_original_frame_is_not_mutated():
    frame = fixture_frame()
    before = frame.copy()
    staging.stage_contracts(frame, {"source_name": "s", "raw_run_id": "r", "staged_at": FIXED})
    pd.testing.assert_frame_equal(frame, before)


def test_schema_drift_stops(tmp_path):
    make_raw_run(tmp_path, fixture_frame().drop(columns=["abc"]))
    with pytest.raises(staging.StagingError, match="missing"):
        stage(tmp_path)


def test_tampered_raw_run_stops(tmp_path):
    run = make_raw_run(tmp_path)
    (run.directory / "file.parquet").write_bytes(b"edited")
    with pytest.raises(RawStoreError):
        stage(tmp_path)


def test_missing_raw_runs_are_reported(tmp_path):
    with pytest.raises(staging.StagingError, match="no raw runs"):
        stage(tmp_path)


def test_data_contract_documents_every_output_column():
    text = (PROJECT_ROOT / "docs" / "data_contract.md").read_text(encoding="utf-8")
    missing = [column for column in staging.CONTRACT_COLUMNS + staging.MEMBER_COLUMNS if f"`{column}`" not in text]
    assert missing == []


def test_cli_stage_defaults():
    args = build_parser().parse_args(["stage"])
    assert args.source == "bettergov_hf"
    assert args.run_id is None
    assert args.rebuild is False
