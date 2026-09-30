import json
from datetime import datetime, timedelta, timezone

import pytest

from src.config import load_settings
from src.extract import dpwh_projects
from src.extract.raw_store import RawRun
from tests.fakes import FakeClock, FakeResponse, FakeSession

API = "https://api.transparency.dpwh.gov.ph"
ENVIRON = {"SCRAPER_CONTACT": "team@example.test"}
STATS_URL = API + load_settings().raw["source"]["stats_path"]


def record(contract_id):
    return {"contractId": contract_id, "description": "ROAD", "budget": 1.0}


def page_body(records, page, total_pages, total_count):
    payload = {"status": 200, "data": {"data": records, "pagination": {"page": page, "totalPages": total_pages, "totalCount": total_count}}}
    return json.dumps(payload).encode("utf-8")


def page_url(page):
    return f"{API}/projects?page={page}&limit=50"


def base_routes():
    return {
        f"{API}/robots.txt": [FakeResponse(404)],
        STATS_URL: [FakeResponse(200, b'{"status": 200, "data": {"totalProjects": 3}}')],
        page_url(1): [FakeResponse(200, page_body([record("A1"), record("A2")], 1, 2, 3))],
        page_url(2): [FakeResponse(200, page_body([record("B1")], 2, 2, 3))],
    }


class TickingNow:
    def __init__(self):
        self.moment = datetime(2026, 9, 30, 14, 0, 0, tzinfo=timezone.utc)

    def __call__(self):
        self.moment += timedelta(seconds=1)
        return self.moment


def make_client(routes):
    clock = FakeClock()
    session = FakeSession(routes)
    settings = load_settings()
    client = dpwh_projects.build_client(settings, ENVIRON, session=session, clock=clock.clock, sleep=clock.sleep)
    return settings, client, session


def run(tmp_path, routes, fetch_stats=True, **kwargs):
    settings, client, session = make_client(routes)
    settings.raw["source"]["fetch_stats"] = fetch_stats
    result = dpwh_projects.run_extract(settings, environ=ENVIRON, client=client, raw_root=tmp_path, now=TickingNow(), log=lambda message: None, **kwargs)
    return result, session


def test_find_records_handles_nested_envelope():
    payload = json.loads(page_body([record("A1")], 1, 1, 1))
    assert dpwh_projects.find_records(payload) == [record("A1")]
    assert dpwh_projects.find_int(payload, dpwh_projects.TOTAL_PAGES_KEYS) == 1


def test_find_records_ignores_unrelated_empty_lists():
    payload = {"sources": [], "data": {"projects": [record("A1")]}}
    assert dpwh_projects.find_records(payload) == [record("A1")]


def test_empty_page_is_detected():
    assert dpwh_projects.is_empty_page({"data": {"data": [], "pagination": {}}})
    assert not dpwh_projects.is_empty_page({"message": "unexpected"})


def test_contact_is_required():
    with pytest.raises(dpwh_projects.ExtractError):
        dpwh_projects.contact_from_env({"SCRAPER_CONTACT": "change_me"})


def test_full_run_stores_stats_and_every_page(tmp_path):
    (raw_run, status), _ = run(tmp_path, base_routes())
    assert status == "complete"
    assert raw_run.verified_files() == {"stats.json", "projects/page_00001.json", "projects/page_00002.json"}
    metadata = raw_run.read_metadata()
    assert metadata["total_pages_reported"] == 2
    assert metadata["total_count_reported"] == 3
    assert metadata["last_session_records"] == 3
    assert metadata["robots"][API]["status"] == 404


def test_max_pages_caps_then_resume_fetches_only_missing(tmp_path):
    (first_run, status), _ = run(tmp_path, base_routes(), max_pages=1)
    assert status == "capped"
    assert first_run.verified_files() == {"stats.json", "projects/page_00001.json"}

    (resumed, status), session = run(tmp_path, base_routes(), resume_run_id=first_run.run_id)
    assert status == "complete"
    fetched = [url for url, _ in session.calls]
    assert page_url(1) not in fetched
    assert STATS_URL not in fetched
    assert page_url(2) in fetched
    assert len(resumed.manifest_entries()) == 3


def test_challenge_mid_run_stops_and_keeps_stored_pages(tmp_path):
    routes = base_routes()
    routes[page_url(2)] = [FakeResponse(403, b"<html>Just a moment</html>", {"content-type": "text/html"})]
    (raw_run, status), _ = run(tmp_path, routes)
    assert status == "stopped"
    assert "challenge" in raw_run.read_metadata()["stop_reason"]
    assert raw_run.verified_files() == {"stats.json", "projects/page_00001.json"}


def test_non_json_response_stops_without_writing(tmp_path):
    routes = base_routes()
    routes[page_url(1)] = [FakeResponse(200, b"<html>maintenance</html>", {"content-type": "application/json"})]
    (raw_run, status), _ = run(tmp_path, routes)
    assert status == "stopped"
    assert raw_run.verified_files() == {"stats.json"}


def test_unexpected_shape_stops_for_review(tmp_path):
    routes = base_routes()
    routes[page_url(1)] = [FakeResponse(200, b'{"message": "changed"}')]
    (raw_run, status), _ = run(tmp_path, routes)
    assert status == "stopped"
    assert "could not find project records" in raw_run.read_metadata()["stop_reason"]


def test_empty_page_ends_run(tmp_path):
    routes = base_routes()
    routes[page_url(1)] = [FakeResponse(200, json.dumps({"data": {"data": [], "pagination": {}}}).encode())]
    (raw_run, status), _ = run(tmp_path, routes)
    assert status == "complete"
    assert raw_run.verified_files() == {"stats.json"}


def test_not_found_stats_stops_without_writing(tmp_path):
    routes = base_routes()
    routes[STATS_URL] = [FakeResponse(404, b'{"message": "Route GET:/stats not found", "statusCode": 404}')]
    (raw_run, status), _ = run(tmp_path, routes)
    assert status == "stopped"
    assert "HTTP 404" in raw_run.read_metadata()["stop_reason"]
    assert raw_run.verified_files() == set()


def test_stats_can_be_disabled(tmp_path):
    routes = base_routes()
    routes[STATS_URL] = [FakeResponse(403, b"<html>blocked</html>", {"content-type": "text/html"})]
    (raw_run, status), session = run(tmp_path, routes, fetch_stats=False, max_pages=1)
    assert status == "capped"
    assert STATS_URL not in [url for url, _ in session.calls]
    assert raw_run.verified_files() == {"projects/page_00001.json"}
    assert raw_run.read_metadata()["stats_fetch_enabled"] is False
