import json
import os

from src.config import PLACEHOLDER_VALUES
from src.extract.http_client import PoliteClient, StopScraping, build_user_agent
from src.extract.raw_store import RawRun, utc_iso, utc_now

RECORD_KEY = "contractId"
RECORD_CONTAINER_KEYS = ("data", "projects", "items", "results", "records")
TOTAL_PAGES_KEYS = ("totalPages", "total_pages", "lastPage")
TOTAL_COUNT_KEYS = ("totalCount", "total_count", "total")
STATS_FILE = "stats.json"


class ExtractError(Exception):
    pass


def find_records(payload, depth=0):
    if depth > 4:
        return None
    if isinstance(payload, list):
        if payload and all(isinstance(item, dict) and RECORD_KEY in item for item in payload):
            return payload
        return None
    if isinstance(payload, dict):
        for value in payload.values():
            found = find_records(value, depth + 1)
            if found is not None:
                return found
    return None


def is_empty_page(payload, depth=0):
    if depth > 4 or not isinstance(payload, dict):
        return False
    for key in RECORD_CONTAINER_KEYS:
        if payload.get(key) == []:
            return True
    return any(is_empty_page(value, depth + 1) for value in payload.values())


def find_int(payload, keys, depth=0):
    if depth > 4 or not isinstance(payload, dict):
        return None
    for key in keys:
        value = payload.get(key)
        if isinstance(value, int) and not isinstance(value, bool):
            return value
    for value in payload.values():
        found = find_int(value, keys, depth + 1)
        if found is not None:
            return found
    return None


def page_file(page):
    return f"projects/page_{page:05d}.json"


def contact_from_env(environ):
    contact = (environ.get("SCRAPER_CONTACT") or "").strip()
    if contact.lower() in PLACEHOLDER_VALUES:
        raise ExtractError("SCRAPER_CONTACT is missing or still a placeholder in .env")
    return contact


def build_client(settings, environ, session=None, clock=None, sleep=None):
    source = settings.raw["source"]
    scraper = settings.raw["scraper"]
    user_agent = build_user_agent(source["user_agent_product"], contact_from_env(environ))
    kwargs = {}
    if clock is not None:
        kwargs["clock"] = clock
    if sleep is not None:
        kwargs["sleep"] = sleep
    return PoliteClient(
        user_agent=user_agent,
        request_delay_seconds=scraper["request_delay_seconds"],
        timeout_seconds=scraper["timeout_seconds"],
        max_retries=scraper["max_retries"],
        backoff_seconds=scraper["backoff_seconds"],
        stop_on_status=scraper.get("stop_on_status", [401, 403]),
        respect_robots_txt=scraper.get("respect_robots_txt", True),
        session=session,
        **kwargs,
    )


def require_ok(response, url):
    if response.status_code != 200:
        raise StopScraping(f"HTTP {response.status_code} from {url}; expected 200, stopping for review")


def parse_json(content, url):
    try:
        return json.loads(content)
    except ValueError as exc:
        preview = content[:200].decode("utf-8", errors="replace")
        raise StopScraping(f"non-JSON response from {url}; first bytes: {preview!r}") from exc


def run_extract(settings, environ=None, max_pages=None, resume_run_id=None, client=None, raw_root=None, now=utc_now, log=print):
    environ = os.environ if environ is None else environ
    source = settings.raw["source"]
    base_url = source["api_base_url"].rstrip("/")
    limit = int(source["page_limit"])
    raw_root = raw_root or settings.paths["raw"]
    client = client or build_client(settings, environ)

    if resume_run_id:
        run = RawRun.open_existing(raw_root, resume_run_id)
        done = run.verified_files()
        metadata = run.read_metadata()
        log(f"Resuming run {run.run_id}: {len(done)} verified raw files already stored")
    else:
        run = RawRun.create(raw_root, now=now)
        done = set()
        metadata = {"run_id": run.run_id, "started_at_utc": utc_iso(now())}
        log(f"Starting run {run.run_id} in {run.directory}")

    metadata.update(
        {
            "api_base_url": base_url,
            "page_limit": limit,
            "user_agent_product": source["user_agent_product"],
            "request_delay_seconds": settings.raw["scraper"]["request_delay_seconds"],
            "status": "running",
            "stop_reason": None,
        }
    )
    run.write_metadata(metadata)

    pages_this_session = 0
    records_this_session = 0
    total_pages = metadata.get("total_pages_reported")
    total_count = metadata.get("total_count_reported")
    status = "running"
    stop_reason = None

    try:
        if STATS_FILE not in done:
            stats_url = f"{base_url}{source['stats_path']}"
            response = client.get(stats_url)
            require_ok(response, stats_url)
            parse_json(response.content, stats_url)
            run.write(STATS_FILE, response.content, stats_url, response.status_code, now())
            log(f"Saved {STATS_FILE} ({len(response.content)} bytes)")

        page = 1
        while True:
            relative = page_file(page)
            if relative in done:
                if total_pages is None:
                    stored = json.loads((run.directory / relative).read_bytes())
                    total_pages = find_int(stored, TOTAL_PAGES_KEYS)
                    total_count = find_int(stored, TOTAL_COUNT_KEYS)
                if total_pages is not None and page >= total_pages:
                    status, stop_reason = "complete", f"all {total_pages} pages stored"
                    break
                page += 1
                continue

            if max_pages is not None and pages_this_session >= max_pages:
                status, stop_reason = "capped", f"--max-pages {max_pages} reached"
                break

            url = f"{base_url}{source['projects_path']}?page={page}&limit={limit}"
            response = client.get(url)
            require_ok(response, url)
            payload = parse_json(response.content, url)
            records = find_records(payload)
            if records is None:
                if is_empty_page(payload):
                    status, stop_reason = "complete", f"page {page} returned no records"
                    break
                raise StopScraping(f"could not find project records in the response from {url}; stopping for review")

            entry = run.write(relative, response.content, url, response.status_code, now())
            pages_this_session += 1
            records_this_session += len(records)
            if total_pages is None:
                total_pages = find_int(payload, TOTAL_PAGES_KEYS)
                total_count = find_int(payload, TOTAL_COUNT_KEYS)
            of_total = f"/{total_pages}" if total_pages else ""
            log(f"[page {page}{of_total}] {len(records)} records, {entry['bytes']} bytes, sha256 {entry['sha256'][:12]}")

            if total_pages is not None and page >= total_pages:
                status, stop_reason = "complete", f"all {total_pages} pages stored"
                break
            page += 1
    except StopScraping as exc:
        status, stop_reason = "stopped", str(exc)
    except KeyboardInterrupt:
        status, stop_reason = "interrupted", "stopped by user"

    metadata.update(
        {
            "status": status,
            "stop_reason": stop_reason,
            "total_pages_reported": total_pages,
            "total_count_reported": total_count,
            "last_session_finished_at_utc": utc_iso(now()),
            "last_session_pages": pages_this_session,
            "last_session_records": records_this_session,
            "last_session_requests": client.request_count,
            "robots": client.robots_log,
        }
    )
    run.write_metadata(metadata)

    log(f"Run {run.run_id} {status}: {stop_reason}")
    log(f"This session: {pages_this_session} pages, {records_this_session} records, {client.request_count} requests")
    if total_count is not None:
        log(f"Portal reports {total_count} projects across {total_pages} pages")
    if status in ("capped", "interrupted", "stopped"):
        log(f"Resume with: python -m src.cli extract --resume {run.run_id}")
    return run, status
