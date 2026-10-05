import os
from datetime import datetime, timezone
from pathlib import Path

from src.config import PROJECT_ROOT, load_settings
from src.extract.raw_store import RawRun, sha256_bytes, utc_iso, utc_now

REQUIRED_KEYS = (
    "path",
    "publisher",
    "dataset_url",
    "file_url",
    "revision",
    "licence",
    "expected_sha256",
    "collection_method",
)


class FileSourceError(Exception):
    pass


def source_config(settings, name):
    sources = settings.raw.get("file_sources") or {}
    if name not in sources:
        known = ", ".join(sorted(sources)) or "none"
        raise FileSourceError(f"unknown file source '{name}'; configured sources: {known}")
    config = sources[name]
    missing = [key for key in REQUIRED_KEYS if not config.get(key)]
    if missing:
        raise FileSourceError(f"file source '{name}' is missing settings: {', '.join(missing)}")
    return config


def lane_root(raw_root, name):
    return Path(raw_root) / f"source={name}"


def find_existing_run(root, sha256):
    if not root.is_dir():
        return None
    for directory in sorted(root.glob("run_id=*")):
        run = RawRun.open_existing(root, directory.name.split("=", 1)[1])
        if any(entry.get("sha256") == sha256 for entry in run.manifest_entries()):
            run.verified_files()
            return run
    return None


def display_path(path, project_root):
    try:
        return str(path.relative_to(project_root))
    except ValueError:
        return str(path)


def run_file_extract(settings, name, raw_root=None, project_root=PROJECT_ROOT, now=utc_now, log=print, run_id=None):
    config = source_config(settings, name)
    project_root = Path(project_root)
    source_path = Path(config["path"])
    if not source_path.is_absolute():
        source_path = project_root / source_path
    if not source_path.is_file():
        raise FileSourceError(f"source file not found: {source_path}")

    content = source_path.read_bytes()
    actual = sha256_bytes(content)
    expected = str(config["expected_sha256"]).strip().lower()
    if actual != expected:
        raise FileSourceError(
            f"checksum mismatch for {source_path.name}: expected {expected}, found {actual}; "
            "the local file is not the published file, nothing was written"
        )
    log(f"Checksum matches the published value: {actual}")

    root = lane_root(raw_root or settings.paths["raw"], name)
    existing = find_existing_run(root, actual)
    if existing is not None:
        log(f"Unchanged: this exact file is already stored and verified in run {existing.run_id}; nothing written")
        return existing, "unchanged"

    run = RawRun.create(root, now=now, run_id=run_id)
    entry = run.write(source_path.name, content, config["file_url"], None, now())
    modified = datetime.fromtimestamp(source_path.stat().st_mtime, tz=timezone.utc)
    metadata = {
        "run_id": run.run_id,
        "source_name": name,
        "status": "complete",
        "publisher": config["publisher"],
        "dataset_url": config["dataset_url"],
        "file_url": config["file_url"],
        "revision": config["revision"],
        "revision_date_utc": config.get("revision_date_utc"),
        "licence": config["licence"],
        "collection_method": config["collection_method"],
        "collected_by_team": False,
        "local_source_path": display_path(source_path, project_root),
        "local_source_modified_utc": utc_iso(modified),
        "sha256": actual,
        "sha256_matches_published": True,
        "bytes": entry["bytes"],
        "ingested_at_utc": entry["fetched_at_utc"],
    }
    run.write_metadata(metadata)
    log(f"Stored {source_path.name} ({entry['bytes']} bytes) in {run.directory}")
    return run, "stored"


def extract_sources(run_id=None, settings=None, sources=None, environ=None, **kwargs):
    settings = settings or load_settings()
    environ = os.environ if environ is None else environ
    run_id = run_id or environ.get("PIPELINE_RUN_ID") or None
    names = sources or sorted(settings.raw.get("file_sources") or {})
    if not names:
        raise FileSourceError("no file sources are configured in config/settings.yml")
    return {name: run_file_extract(settings, name, run_id=run_id, **kwargs) for name in names}
