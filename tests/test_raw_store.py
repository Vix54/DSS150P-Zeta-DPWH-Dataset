import json
from datetime import datetime, timezone

import pytest

from src.extract.raw_store import RawRun, RawStoreError, sha256_bytes

FIXED = datetime(2026, 9, 30, 14, 30, 0, tzinfo=timezone.utc)


def fixed_now():
    return FIXED


def test_create_uses_utc_run_id(tmp_path):
    run = RawRun.create(tmp_path, now=fixed_now)
    assert run.run_id == "20260930T143000Z"
    assert run.directory == tmp_path / "run_id=20260930T143000Z"


def test_create_refuses_existing_run(tmp_path):
    RawRun.create(tmp_path, now=fixed_now)
    with pytest.raises(RawStoreError):
        RawRun.create(tmp_path, now=fixed_now)


def test_write_stores_bytes_unchanged_and_records_manifest(tmp_path):
    run = RawRun.create(tmp_path, now=fixed_now)
    content = b'{"data": [1, 2, 3]}'
    entry = run.write("projects/page_00001.json", content, "https://api.example.test/p", 200, FIXED)
    assert (run.directory / "projects/page_00001.json").read_bytes() == content
    assert entry["sha256"] == sha256_bytes(content)
    assert entry["fetched_at_utc"] == "2026-09-30T14:30:00Z"
    lines = run.manifest_path.read_text(encoding="utf-8").splitlines()
    assert json.loads(lines[0])["file"] == "projects/page_00001.json"


def test_write_never_overwrites(tmp_path):
    run = RawRun.create(tmp_path, now=fixed_now)
    run.write("stats.json", b"{}", "u", 200, FIXED)
    with pytest.raises(RawStoreError):
        run.write("stats.json", b"{}", "u", 200, FIXED)


def test_verified_files_detects_tampering(tmp_path):
    run = RawRun.create(tmp_path, now=fixed_now)
    run.write("stats.json", b'{"a": 1}', "u", 200, FIXED)
    assert run.verified_files() == {"stats.json"}
    (run.directory / "stats.json").write_bytes(b'{"a": 2}')
    with pytest.raises(RawStoreError, match="checksum mismatch"):
        run.verified_files()


def test_verified_files_detects_missing_file(tmp_path):
    run = RawRun.create(tmp_path, now=fixed_now)
    run.write("stats.json", b"{}", "u", 200, FIXED)
    (run.directory / "stats.json").unlink()
    with pytest.raises(RawStoreError, match="missing"):
        run.verified_files()


def test_open_existing_requires_run(tmp_path):
    with pytest.raises(RawStoreError):
        RawRun.open_existing(tmp_path, "20990101T000000Z")
