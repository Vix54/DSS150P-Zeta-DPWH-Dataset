import json
import os
import re

from src.extract.file_source import (
    CHECKSUM_ORIGINS,
    FileSourceError,
    SHA256_PATTERN,
    build_download_client,
    find_existing_run,
    lane_root,
)
from src.extract.http_client import StopScraping
from src.extract.raw_store import RawRun, sha256_bytes, utc_now
from src.transform.jsonstat import JsonStatError, jsonstat2_frame
from src.transform.pxweb import PxWebError, pxweb_json_frame

REQUIRED_KEYS = (
    "publisher",
    "dataset_url",
    "api_url",
    "terms_url",
    "file_name",
    "response_format",
    "revision",
    "licence",
    "collection_method",
)
SUPPORTED_FORMATS = {"json-stat2", "json"}
DEFAULT_METADATA_FILE = "table_metadata.json"
FILE_NAME_PATTERN = re.compile(r"[A-Za-z0-9._-]+\.json")


def api_config(settings, name):
    sources = settings.raw.get("api_sources") or {}
    if name not in sources:
        known = ", ".join(sorted(sources)) or "none"
        raise FileSourceError(f"unknown API source '{name}'; configured API sources: {known}")
    config = sources[name]
    missing = [key for key in REQUIRED_KEYS if not config.get(key)]
    if missing:
        raise FileSourceError(f"API source '{name}' is missing settings: {', '.join(missing)}")
    if not isinstance(config.get("query"), list):
        raise FileSourceError(f"API source '{name}' needs a 'query' list (the PX-Web selection; [] selects the whole table)")
    if config["response_format"] not in SUPPORTED_FORMATS:
        raise FileSourceError(f"API source '{name}' has response_format '{config['response_format']}'; supported: {', '.join(sorted(SUPPORTED_FORMATS))}")
    if not FILE_NAME_PATTERN.fullmatch(config["file_name"]):
        raise FileSourceError(f"API source '{name}' needs a plain file_name ending in .json")
    if config["response_format"] == "json" and (not FILE_NAME_PATTERN.fullmatch(metadata_file_name(config)) or metadata_file_name(config) == config["file_name"]):
        raise FileSourceError(f"API source '{name}' needs a plain metadata_file_name ending in .json that differs from file_name")
    if config.get("robots_txt_challenge", "stop") not in ("stop", "documented_api"):
        raise FileSourceError(f"API source '{name}' has robots_txt_challenge '{config['robots_txt_challenge']}'; use 'stop' or 'documented_api'")
    if config.get("robots_txt_challenge") == "documented_api" and not config.get("api_documentation_url"):
        raise FileSourceError(f"API source '{name}' may only use robots_txt_challenge: documented_api with an api_documentation_url")
    origin = config.get("checksum_origin", "recorded_at_download")
    if origin not in CHECKSUM_ORIGINS:
        raise FileSourceError(f"API source '{name}' has unknown checksum_origin '{origin}'")
    return config


def metadata_file_name(config):
    return str(config.get("metadata_file_name") or DEFAULT_METADATA_FILE)


def parse_response(config, content, metadata_content):
    if config["response_format"] == "json":
        return pxweb_json_frame(json.loads(content.decode("utf-8-sig")), json.loads(metadata_content.decode("utf-8-sig")), config["query"])
    return jsonstat2_frame(json.loads(content.decode("utf-8")))


def fetch_metadata(client, url):
    try:
        response = client.get(url)
    except StopScraping as exc:
        raise FileSourceError(f"API metadata request stopped: {exc}; nothing was written") from exc
    if response.status_code != 200:
        raise FileSourceError(f"API metadata request returned HTTP {response.status_code} for {url}; nothing was written")
    return response


def request_payload(config):
    return {"query": config["query"], "response": {"format": config["response_format"]}}


def recorded_checksum(config):
    value = str(config.get("expected_sha256") or "").strip().lower()
    return value if SHA256_PATTERN.fullmatch(value) else None


def run_api_extract(settings, name, client=None, environ=None, raw_root=None, now=utc_now, log=print, run_id=None, **_):
    config = api_config(settings, name)
    environ = os.environ if environ is None else environ
    client = client or build_download_client(settings, environ, accept="application/json", robots_challenge=config.get("robots_txt_challenge", "stop"))
    payload = request_payload(config)
    url = config["api_url"]
    log(f"Requesting {name} from {url} ({config['response_format']})")
    metadata_response = fetch_metadata(client, url) if config["response_format"] == "json" else None
    try:
        response = client.post(url, payload)
    except StopScraping as exc:
        raise FileSourceError(f"API request stopped: {exc}; nothing was written") from exc
    if response.status_code != 200:
        raise FileSourceError(f"API returned HTTP {response.status_code} for {url}; nothing was written")
    content = response.content
    try:
        frame = parse_response(config, content, metadata_response.content if metadata_response is not None else None)
    except (UnicodeDecodeError, ValueError, JsonStatError, PxWebError) as exc:
        raise FileSourceError(f"API response from {url} is not a valid {config['response_format']} dataset ({exc}); nothing was written") from exc
    actual = sha256_bytes(content)
    expected = recorded_checksum(config)
    if expected is None:
        raise FileSourceError(
            f"no expected_sha256 recorded yet for {name}. The API returned {len(frame)} values with SHA-256 {actual}. "
            f"Review the response, record this value as api_sources.{name}.expected_sha256 in config/settings.yml, and run again; nothing was written"
        )
    if actual != expected:
        raise FileSourceError(
            f"API response for {name} does not match the recorded SHA-256 (expected {expected}, found {actual}); "
            "the publisher may have revised the table. Review the change and update expected_sha256; nothing was written"
        )
    log(f"Checksum matches {CHECKSUM_ORIGINS[config.get('checksum_origin', 'recorded_at_download')]}: {actual}")

    root = lane_root(raw_root or settings.paths["raw"], name)
    existing = find_existing_run(root, actual)
    if existing is not None:
        log(f"Unchanged: this exact response is already stored and verified in run {existing.run_id}; nothing written")
        return existing, "unchanged"

    run = RawRun.create(root, now=now, run_id=run_id)
    entry = run.write(config["file_name"], content, url, response.status_code, now())
    if metadata_response is not None:
        run.write(metadata_file_name(config), metadata_response.content, url, metadata_response.status_code, now())
    metadata = {
        "run_id": run.run_id,
        "source_name": name,
        "status": "complete",
        "publisher": config["publisher"],
        "dataset_url": config["dataset_url"],
        "api_url": url,
        "api_documentation_url": config.get("api_documentation_url"),
        "terms_url": config["terms_url"],
        "rate_limit": config.get("rate_limit"),
        "request": payload,
        "revision": config["revision"],
        "licence": config["licence"],
        "attribution": config.get("attribution"),
        "collection_method": config["collection_method"],
        "collected_by_team": True,
        "retrieval": "api",
        "robots": getattr(client, "robots_log", {}),
        "requests_made": getattr(client, "request_count", None),
        "sha256": actual,
        "checksum_origin": config.get("checksum_origin", "recorded_at_download"),
        "sha256_matches_expected": True,
        "values": int(len(frame)),
        "metadata_file": metadata_file_name(config) if metadata_response is not None else None,
        "bytes": entry["bytes"],
        "ingested_at_utc": entry["fetched_at_utc"],
    }
    run.write_metadata(metadata)
    log(f"Stored {config['file_name']} ({entry['bytes']} bytes, {len(frame)} values) in {run.directory}")
    return run, "stored"
