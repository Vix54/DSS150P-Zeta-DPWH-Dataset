import json
from datetime import datetime, timedelta, timezone

import pandas as pd
import pytest

from src.cli import build_parser
from src.config import Settings, load_settings
from src.extract.file_source import lane_root
from src.extract.raw_store import RawRun
from src.transform import curated, staging
from tests.test_staging import make_raw_run, row

FIXED = datetime(2026, 10, 6, 9, 0, 0, tzinfo=timezone.utc)
PSGC_ROWS = [
    ("1300000000", "National Capital Region (NCR)", "Reg"),
    ("1380100000", "City of Caloocan", "City"),
    ("0100000000", "Region I (Ilocos Region)", "Reg"),
    ("0400000000", "Region IV-A (CALABARZON)", "Reg"),
    ("1700000000", "MIMAROPA Region", "Reg"),
]


class Clock:
    def __init__(self, start=FIXED):
        self.moment = start

    def __call__(self):
        self.moment += timedelta(seconds=1)
        return self.moment


def contract_frame():
    rows = [
        row(contractId="A0000001", region="Region IV-A"),
        row(contractId="B0000001", region="Region IV-B"),
        row(contractId="C0000001", region="Central Office"),
        row(contractId="N0000001", region="National Capital Region", status="On-Going", progress=50.0, completionDate=None, expiryDate="2025-06-30"),
        row(contractId="N0000002", region="National Capital Region", status="On-Going", progress=50.0, completionDate=None, expiryDate="2026-06-30"),
        row(contractId="P0000001", progress=-100.0),
        row(
            contractId="J0000001",
            contractor="A.M. ORETA & CO., INC. (108) / ALLENCON DEVELOPMENT CORPORATION (12302)",
            winnerNames="ALLENCON DEVELOPMENT CORPORATION, A.M. ORETA & CO., INC.",
        ),
    ]
    return pd.DataFrame(rows, columns=list(staging.SOURCE_COLUMNS))


def make_settings(tmp_path):
    raw = {
        "file_sources": {"bettergov_hf": {"revision_date_utc": "2026-01-22T00:40:18Z"}},
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
    }
    paths = {name: tmp_path / name for name in ("raw", "staging", "curated", "quarantine")}
    return Settings(raw=raw, paths=paths)


def make_psgc_run(tmp_path, rows=PSGC_ROWS):
    workbook = tmp_path / "psgc.xlsx"
    sheet = pd.DataFrame(rows, columns=["10-digit PSGC", "Name", "Geographic Level"])
    with pd.ExcelWriter(workbook) as writer:
        pd.DataFrame({"Title:": ["PSGC"]}).to_excel(writer, sheet_name="Metadata", index=False)
        sheet.to_excel(writer, sheet_name="PSGC", index=False)
    run = RawRun.create(lane_root(tmp_path / "raw", "psa_psgc"), now=lambda: FIXED)
    run.write("PSGC.xlsx", workbook.read_bytes(), "https://example.test/psgc.xlsx", None, FIXED)
    return run


def build(tmp_path, frame=None):
    settings = make_settings(tmp_path)
    make_raw_run(tmp_path, contract_frame() if frame is None else frame)
    make_psgc_run(tmp_path)
    staging.run_staging(settings, now=lambda: FIXED, log=lambda message: None, environ={})
    return settings


def curate(settings, **kwargs):
    kwargs.setdefault("environ", {})
    kwargs.setdefault("now", Clock())
    return curated.run_curated(settings, log=lambda message: None, **kwargs)


def outputs(settings, report):
    directory = settings.paths["curated"] / f"run_id={report['staging_run_id']}"
    quarantine = settings.paths["quarantine"] / "curated" / f"run_id={report['staging_run_id']}"
    return (
        pd.read_parquet(directory / curated.CURATED_CONTRACTS_FILE),
        pd.read_parquet(directory / curated.CURATED_MEMBERS_FILE),
        pd.read_parquet(directory / curated.CURATED_REGIONS_FILE),
        pd.read_parquet(quarantine / curated.CURATED_QUARANTINE_CONTRACTS),
    )


def by_id(frame, contract_id):
    return frame.loc[frame["contract_id"] == contract_id].iloc[0]


def test_region_keys_cover_numerals_names_and_abbreviations():
    assert curated.region_keys("Region IV-A (CALABARZON)") == {"REGION IV-A", "CALABARZON"}
    assert curated.region_keys("National Capital Region (NCR)") == {"NATIONAL CAPITAL REGION", "NCR"}
    assert "REGION I" not in curated.region_keys("Region IV-A")


def test_match_regions_uses_aliases_and_rejects_unknown_labels():
    regions = pd.DataFrame([(code, name) for code, name, level in PSGC_ROWS if level == "Reg"], columns=["psgc_code", "region_name"])
    matches = curated.match_regions(["Region IV-B", "Central Office", "Region I"], regions, {"Region IV-B": "MIMAROPA"})
    assert matches["Region IV-B"]["psgc_code"] == "1700000000"
    assert matches["Region I"]["psgc_code"] == "0100000000"
    assert matches["Central Office"]["status"] == "unmatched"


def test_every_staged_row_is_curated_or_quarantined(tmp_path):
    settings = build(tmp_path)
    report, status = curate(settings)
    assert status == "curated"
    assert report["rows_in"] == 7
    assert report["rows_curated"] == 6
    assert report["rows_quarantined"] == 1
    contracts, _, _, rejected = outputs(settings, report)
    assert rejected["contract_id"].tolist() == ["P0000001"]
    assert rejected["error_codes"].tolist() == ["Q_PROGRESS_OUT_OF_RANGE"]
    assert "P0000001" not in set(contracts["contract_id"])


def test_psgc_region_flags_never_drop_rows(tmp_path):
    settings = build(tmp_path)
    report, _ = curate(settings)
    contracts, _, regions, _ = outputs(settings, report)
    assert by_id(contracts, "A0000001")["region_psgc_code"] == "0400000000"
    assert by_id(contracts, "B0000001")["region_psgc_name"] == "MIMAROPA Region"
    central = by_id(contracts, "C0000001")
    assert bool(central["region_matches_psgc"]) is False
    assert "W_REGION_NOT_IN_PSGC" in central["warning_codes"]
    assert report["unmatched_region_labels"] == ["Central Office"]
    assert set(regions["match_status"]) == {"matched", "unmatched"}


def test_metrics_and_delay_flag(tmp_path):
    settings = build(tmp_path)
    report, _ = curate(settings)
    contracts, _, _, _ = outputs(settings, report)
    first = by_id(contracts, "A0000001")
    assert first["award_savings_php"] == pytest.approx(168300.0)
    assert first["award_to_abc_pct"] == pytest.approx(96.6)
    assert first["project_cost"] == 4950000.0
    assert first["physical_accomplishment"] == 100.0
    assert bool(by_id(contracts, "N0000001")["is_delayed"]) is True
    assert bool(by_id(contracts, "N0000002")["is_delayed"]) is False
    assert bool(first["is_delayed"]) is False
    assert report["delay_as_of_date"] == "2026-01-22"


def test_team_column_names_and_lineage(tmp_path):
    settings = build(tmp_path)
    report, _ = curate(settings, pipeline_run_id="airflow__20261006T020000")
    contracts, members, _, _ = outputs(settings, report)
    assert list(contracts.columns) == list(curated.CURATED_COLUMNS)
    assert {"contract_id", "project_cost", "physical_accomplishment", "start_date", "infra_year", "is_delayed", "status_name"} <= set(contracts.columns)
    assert set(contracts["pipeline_run_id"]) == {"airflow__20261006T020000"}
    assert contracts["record_hash"].str.fullmatch(r"[0-9a-f]{64}").all()
    assert members.loc[members["contract_id"] == "J0000001", "member_position"].tolist() == [1, 2]


def test_record_hash_is_stable_across_rebuilds(tmp_path):
    settings = build(tmp_path)
    first, _ = curate(settings, now=Clock(FIXED))
    before = outputs(settings, first)[0].set_index("contract_id")["record_hash"]
    second, status = curate(settings, rebuild=True, now=Clock(FIXED + timedelta(days=3)))
    after = outputs(settings, second)[0].set_index("contract_id")["record_hash"]
    assert status == "curated"
    assert first["processed_at_utc"] != second["processed_at_utc"]
    pd.testing.assert_series_equal(before, after)


def test_record_hash_changes_when_business_values_change(tmp_path):
    settings = build(tmp_path)
    report, _ = curate(settings)
    contracts, members, _, _ = outputs(settings, report)
    staged = pd.read_parquet(settings.paths["staging"] / "source=bettergov_hf" / f"run_id={report['staging_run_id']}" / staging.CONTRACTS_FILE)
    changed = staged.copy()
    changed.loc[changed["contract_id"] == "A0000001", "budget_php"] = 1.0
    regions = pd.DataFrame([(code, name) for code, name, level in PSGC_ROWS if level == "Reg"], columns=["psgc_code", "region_name"])
    context = {"pipeline_run_id": "x", "processed_at": FIXED, "delay_as_of_date": "2026-01-22", "region_aliases": {"Region IV-B": "MIMAROPA"}}
    recomputed = curated.curate_frames(changed, members, regions, context)[0].set_index("contract_id")["record_hash"]
    original = contracts.set_index("contract_id")["record_hash"]
    assert recomputed["A0000001"] != original["A0000001"]
    assert recomputed["N0000001"] == original["N0000001"]


def test_rerun_is_unchanged_until_rebuild(tmp_path):
    settings = build(tmp_path)
    curate(settings)
    _, status = curate(settings)
    assert status == "unchanged"


def test_modified_staging_output_stops_curation(tmp_path):
    settings = build(tmp_path)
    staging_dir = next((settings.paths["staging"] / "source=bettergov_hf").glob("run_id=*"))
    frame = pd.read_parquet(staging_dir / staging.CONTRACTS_FILE)
    frame.head(1).to_parquet(staging_dir / staging.CONTRACTS_FILE, index=False)
    with pytest.raises(curated.CuratedError, match="checksum mismatch"):
        curate(settings)


def test_missing_staging_run_is_reported(tmp_path):
    settings = make_settings(tmp_path)
    with pytest.raises(curated.CuratedError, match="no staging runs"):
        curate(settings)


def test_original_frames_are_not_mutated(tmp_path):
    settings = build(tmp_path)
    staging_dir = next((settings.paths["staging"] / "source=bettergov_hf").glob("run_id=*"))
    contracts = pd.read_parquet(staging_dir / staging.CONTRACTS_FILE)
    members = pd.read_parquet(staging_dir / staging.MEMBERS_FILE)
    before_contracts, before_members = contracts.copy(), members.copy()
    regions = pd.DataFrame([(code, name) for code, name, level in PSGC_ROWS if level == "Reg"], columns=["psgc_code", "region_name"])
    context = {"pipeline_run_id": "x", "processed_at": FIXED, "delay_as_of_date": "2026-01-22", "region_aliases": {}}
    curated.curate_frames(contracts, members, regions, context)
    pd.testing.assert_frame_equal(contracts, before_contracts)
    pd.testing.assert_frame_equal(members, before_members)


def test_project_settings_define_the_curated_step():
    config = load_settings().raw["curated"]
    assert config["reference_source"] == "psa_psgc"
    assert config["psgc_sheet"] == "PSGC"
    assert "openpyxl" in load_settings().raw["required_packages"]


def test_cli_curate_defaults():
    args = build_parser().parse_args(["curate"])
    assert args.run_id is None
    assert args.rebuild is False


def test_delay_uses_expiry_date_not_completion_date(tmp_path):
    frame = pd.DataFrame(
        [
            row(contractId="E0000001", status="On-Going", progress=40.0, completionDate=None, expiryDate="2025-01-31"),
            row(contractId="E0000002", status="On-Going", progress=40.0, completionDate="2025-01-31", expiryDate="2026-12-31"),
            row(contractId="E0000003", status="Completed", progress=100.0, expiryDate="2025-01-31"),
        ],
        columns=list(staging.SOURCE_COLUMNS),
    )
    settings = build(tmp_path, frame)
    report, _ = curate(settings)
    contracts, _, _, _ = outputs(settings, report)
    assert bool(by_id(contracts, "E0000001")["is_delayed"]) is True
    assert bool(by_id(contracts, "E0000002")["is_delayed"]) is False
    assert bool(by_id(contracts, "E0000003")["is_delayed"]) is False
    assert report["delayed_contracts"] == 1
