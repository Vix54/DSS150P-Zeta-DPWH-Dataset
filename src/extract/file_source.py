import os
import re
from datetime import datetime, timezone
from pathlib import Path

from src.config import PROJECT_ROOT, load_settings
from src.extract.http_client import StopScraping, PoliteClient, build_user_agent
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
CHECKSUM_ORIGINS = {
    "published": "the value published by the source",
    "recorded_at_download": "the value recorded when the file was downloaded",
}
SHA256_PATTERN = re.compile(r"[0-9a-f]{64}")
PLACEHOLDER_VALUES = {"", "change_me", "changeme", "todo", "tbd", "your_email_here"}


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
    origin = config.get("checksum_origin", "published")
    if origin not in CHECKSUM_ORIGINS:
        raise FileSourceError(f"file source '{name}' has unknown checksum_origin '{origin}'; use one of: {', '.join(sorted(CHECKSUM_ORIGINS))}")
    if not SHA256_PATTERN.fullmatch(str(config["expected_sha256"]).strip().lower()):
        raise FileSourceError(f"file source '{name}' needs a 64-character SHA-256 in expected_sha256 (run sha256sum on the downloaded file and record it in config/settings.yml)")
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


def contact_from_env(environ):
    contact = (environ.get("SCRAPER_CONTACT") or "").strip()
    if contact.lower() in PLACEHOLDER_VALUES:
        raise FileSourceError("SCRAPER_CONTACT is missing or still a placeholder in .env; automated retrieval identifies itself with this contact")
    return contact


def build_download_client(settings, environ, accept="*/*", robots_challenge="stop", session=None, clock=None, sleep=None):
    scraper = settings.raw["scraper"]
    product = settings.raw["source"]["user_agent_product"]
    kwargs = {}
    if clock is not None:
        kwargs["clock"] = clock
    if sleep is not None:
        kwargs["sleep"] = sleep
    return PoliteClient(
        user_agent=build_user_agent(product, contact_from_env(environ)),
        request_delay_seconds=scraper["request_delay_seconds"],
        timeout_seconds=max(float(scraper["timeout_seconds"]), 300.0),
        max_retries=scraper["max_retries"],
        backoff_seconds=scraper["backoff_seconds"],
        stop_on_status=scraper.get("stop_on_status", [401, 403]),
        respect_robots_txt=scraper.get("respect_robots_txt", True),
        accept=accept,
        robots_challenge=robots_challenge,
        session=session,
        **kwargs,
    )


def download_file(config, source_path, client, expected, log):
    url = config["file_url"]
    log(f"Downloading {source_path.name} from {url}")
    try:
        response = client.get(url)
    except StopScraping as exc:
        raise FileSourceError(f"automated download stopped: {exc}; nothing was saved") from exc
    if response.status_code != 200:
        raise FileSourceError(f"automated download of {url} returned HTTP {response.status_code}; nothing was saved")
    content = response.content
    actual = sha256_bytes(content)
    if actual != expected:
        raise FileSourceError(f"downloaded file does not match the expected SHA-256 (expected {expected}, found {actual}); nothing was saved")
    source_path.parent.mkdir(parents=True, exist_ok=True)
    temporary = source_path.with_name(source_path.name + ".part")
    temporary.write_bytes(content)
    os.replace(temporary, source_path)
    log(f"Downloaded {len(content)} bytes to {source_path}")
    return len(content)


def display_path(path, project_root):
    try:
        return str(path.relative_to(project_root))
    except ValueError:
        return str(path)


def run_file_extract(settings, name, raw_root=None, project_root=PROJECT_ROOT, now=utc_now, log=print, run_id=None, download=False, client=None, environ=None):
    config = source_config(settings, name)
    project_root = Path(project_root)
    source_path = Path(config["path"])
    if not source_path.is_absolute():
        source_path = project_root / source_path
    expected = str(config["expected_sha256"]).strip().lower()
    downloaded = False
    if download:
        if source_path.is_file() and sha256_bytes(source_path.read_bytes()) == expected:
            log(f"Already present: {source_path.name} matches the expected SHA-256; no download needed")
        elif config.get("download_allowed"):
            client = client or build_download_client(settings, os.environ if environ is None else environ)
            download_file(config, source_path, client, expected, log)
            downloaded = True
        else:
            log(f"No automated download for {name}: download it by hand from {config['dataset_url']} into {config['path']}")
    if not source_path.is_file():
        raise FileSourceError(f"source file not found: {source_path}")

    content = source_path.read_bytes()
    actual = sha256_bytes(content)
    origin = config.get("checksum_origin", "published")
    if actual != expected:
        raise FileSourceError(
            f"checksum mismatch for {source_path.name}: expected {expected}, found {actual}; "
            f"the local file does not match {CHECKSUM_ORIGINS[origin]}, nothing was written"
        )
    log(f"Checksum matches {CHECKSUM_ORIGINS[origin]}: {actual}")

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
        "automated_download_available": bool(config.get("download_allowed")),
        "downloaded_this_run": downloaded,
        "sha256": actual,
        "checksum_origin": origin,
        "sha256_matches_published": origin == "published",
        "sha256_matches_expected": True,
        "attribution": config.get("attribution"),
        "bytes": entry["bytes"],
        "ingested_at_utc": entry["fetched_at_utc"],
    }
    run.write_metadata(metadata)
    log(f"Stored {source_path.name} ({entry['bytes']} bytes) in {run.directory}")
    return run, "stored"


def configured_sources(settings):
    files = sorted(settings.raw.get("file_sources") or {})
    apis = sorted(settings.raw.get("api_sources") or {})
    return files, apis


def extract_sources(run_id=None, settings=None, sources=None, environ=None, download=False, **kwargs):
    from src.extract.api_source import run_api_extract

    settings = settings or load_settings()
    environ = os.environ if environ is None else environ
    run_id = run_id or environ.get("PIPELINE_RUN_ID") or None
    files, apis = configured_sources(settings)
    names = sources or files + apis
    if not names:
        raise FileSourceError("no file or API sources are configured in config/settings.yml")
    unknown = [name for name in names if name not in files and name not in apis]
    if unknown:
        known = ", ".join(files + apis) or "none"
        raise FileSourceError(f"unknown source '{unknown[0]}'; configured sources: {known}")
    results = {}
    for name in names:
        if name in apis:
            results[name] = run_api_extract(settings, name, run_id=run_id, environ=environ, **kwargs)
        else:
            results[name] = run_file_extract(settings, name, run_id=run_id, download=download, environ=environ, **kwargs)
    return results
