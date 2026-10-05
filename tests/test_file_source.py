import json
from datetime import datetime, timedelta, timezone

import pytest

from src.cli import build_parser
from src.config import Settings, load_settings
from src.extract import dpwh_projects
from src.extract.file_source import FileSourceError, lane_root, run_file_extract, source_config
from src.extract.raw_store import RawStoreError, sha256_bytes

CONTENT = b"PAR1-fake-parquet-bytes-PAR1"
PUBLISHED_SHA256 = "953f0bf99d162c062210219cc5f75c22df85049c24c0dae602c1f4dc976a7c97"


class TickingNow:
    def __init__(self):
        self.moment = datetime(2026, 10, 5, 8, 0, 0, tzinfo=timezone.utc)

    def __call__(self):
        self.moment += timedelta(seconds=1)
        return self.moment


def source_entry(path, expected_sha256):
    return {
        "path": str(path),
        "publisher": "Example Publisher",
        "dataset_url": "https://example.test/dataset",
        "file_url": "https://example.test/dataset/file.parquet",
        "revision": "abc123",
        "revision_date_utc": "2026-01-22T00:40:18Z",
        "licence": "CC0-1.0",
        "expected_sha256": expected_sha256,
        "collection_method": "Published by the example publisher.",
    }


def make_settings(tmp_path, content=CONTENT, expected=None):
    source_file = tmp_path / "source" / "file.parquet"
    source_file.parent.mkdir(parents=True, exist_ok=True)
    source_file.write_bytes(content)
    expected = expected or sha256_bytes(content)
    raw = {"file_sources": {"example": source_entry(source_file, expected)}}
    return Settings(raw=raw, paths={"raw": tmp_path / "raw"}), source_file


def extract(settings, tmp_path, now=None):
    return run_file_extract(settings, "example", project_root=tmp_path, now=now or TickingNow(), log=lambda message: None)


def test_stores_file_unchanged_in_its_own_lane(tmp_path):
    settings, _ = make_settings(tmp_path)
    run, status = extract(settings, tmp_path)
    assert status == "stored"
    assert run.directory.parent == lane_root(tmp_path / "raw", "example")
    assert (run.directory / "file.parquet").read_bytes() == CONTENT
    assert run.verified_files() == {"file.parquet"}
    entry = run.manifest_entries()[0]
    assert entry["url"] == "https://example.test/dataset/file.parquet"
    assert entry["sha256"] == sha256_bytes(CONTENT)
    assert entry["status_code"] is None


def test_metadata_records_provenance(tmp_path):
    settings, _ = make_settings(tmp_path)
    run, _ = extract(settings, tmp_path)
    metadata = json.loads(run.metadata_path.read_text(encoding="utf-8"))
    assert metadata["source_name"] == "example"
    assert metadata["licence"] == "CC0-1.0"
    assert metadata["revision"] == "abc123"
    assert metadata["collected_by_team"] is False
    assert metadata["sha256_matches_published"] is True
    assert metadata["local_source_path"] == "source/file.parquet"
    assert metadata["ingested_at_utc"].endswith("Z")


def test_checksum_mismatch_stops_and_writes_nothing(tmp_path):
    settings, _ = make_settings(tmp_path, expected="0" * 64)
    with pytest.raises(FileSourceError, match="checksum mismatch"):
        extract(settings, tmp_path)
    assert not (tmp_path / "raw").exists()


def test_rerun_with_same_file_is_unchanged(tmp_path):
    settings, _ = make_settings(tmp_path)
    now = TickingNow()
    first, _ = extract(settings, tmp_path, now=now)
    second, status = extract(settings, tmp_path, now=now)
    assert status == "unchanged"
    assert second.run_id == first.run_id
    assert len(list(lane_root(tmp_path / "raw", "example").glob("run_id=*"))) == 1


def test_rerun_detects_tampered_raw_copy(tmp_path):
    settings, _ = make_settings(tmp_path)
    run, _ = extract(settings, tmp_path)
    (run.directory / "file.parquet").write_bytes(b"edited")
    with pytest.raises(RawStoreError, match="checksum mismatch"):
        extract(settings, tmp_path)


def test_missing_source_file_is_reported(tmp_path):
    settings, source_file = make_settings(tmp_path)
    source_file.unlink()
    with pytest.raises(FileSourceError, match="not found"):
        extract(settings, tmp_path)


def test_unknown_source_is_reported(tmp_path):
    settings, _ = make_settings(tmp_path)
    with pytest.raises(FileSourceError, match="unknown file source"):
        run_file_extract(settings, "missing", project_root=tmp_path, log=lambda message: None)


def test_incomplete_source_settings_are_reported(tmp_path):
    settings, _ = make_settings(tmp_path)
    del settings.raw["file_sources"]["example"]["licence"]
    with pytest.raises(FileSourceError, match="licence"):
        source_config(settings, "example")


def test_project_settings_define_the_bettergov_release():
    config = source_config(load_settings(), "bettergov_hf")
    assert config["licence"] == "CC0-1.0"
    assert config["expected_sha256"] == PUBLISHED_SHA256
    assert config["revision"] in config["file_url"]
    assert "curl-cffi" in config["collection_method"]


def test_api_runs_use_their_own_lane():
    settings = load_settings()
    assert dpwh_projects.default_raw_root(settings) == settings.paths["raw"] / "source=dpwh_api"


def test_cli_extract_file_defaults_to_bettergov():
    args = build_parser().parse_args(["extract-file"])
    assert args.source == "bettergov_hf"
