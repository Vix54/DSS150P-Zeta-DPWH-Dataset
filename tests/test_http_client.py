import pytest

from src.extract.http_client import PoliteClient, StopScraping, Throttle, build_user_agent, is_challenge

from tests.fakes import FakeClock, FakeResponse, FakeSession

API = "https://api.example.test"


def make_client(routes, clock=None, **overrides):
    clock = clock or FakeClock()
    options = {
        "user_agent": build_user_agent("TestBot/1.0", "team@example.test"),
        "request_delay_seconds": 1.5,
        "timeout_seconds": 5,
        "max_retries": 2,
        "backoff_seconds": 5,
        "stop_on_status": [401, 403],
    }
    options.update(overrides)
    session = FakeSession(routes)
    return PoliteClient(session=session, clock=clock.clock, sleep=clock.sleep, **options), session, clock


def test_throttle_enforces_minimum_interval():
    clock = FakeClock()
    throttle = Throttle(1.5, clock=clock.clock, sleep=clock.sleep)
    throttle.wait()
    clock.now += 0.5
    throttle.wait()
    assert clock.sleeps == [pytest.approx(1.0)]


def test_user_agent_includes_contact():
    assert build_user_agent("TestBot/1.0", "team@example.test") == "TestBot/1.0 (+contact: team@example.test)"


def test_missing_robots_allows_fetch_and_sends_identity():
    client, session, _ = make_client({
        f"{API}/robots.txt": [FakeResponse(404)],
        f"{API}/projects?page=1": [FakeResponse(200, b'{"ok": true}')],
    })
    response = client.get(f"{API}/projects?page=1")
    assert response.status_code == 200
    assert client.robots_log[API]["status"] == 404
    assert session.calls[1][1]["User-Agent"].endswith("(+contact: team@example.test)")


def test_robots_disallow_stops_before_request():
    rules = b"User-agent: *\nDisallow: /projects\n"
    client, session, _ = make_client({
        f"{API}/robots.txt": [FakeResponse(200, rules, {"content-type": "text/plain"})],
        f"{API}/projects?page=1": [FakeResponse(200)],
    })
    with pytest.raises(StopScraping, match="robots.txt disallows"):
        client.get(f"{API}/projects?page=1")
    assert [url for url, _ in session.calls] == [f"{API}/robots.txt"]


def test_robots_wildcard_rules_are_respected():
    rules = b"User-agent: *\nDisallow: /*?page=\n"
    client, _, _ = make_client({
        f"{API}/robots.txt": [FakeResponse(200, rules, {"content-type": "text/plain"})],
    })
    with pytest.raises(StopScraping, match="robots.txt disallows"):
        client.get(f"{API}/projects?page=2")


def test_robots_crawl_delay_raises_interval():
    rules = b"User-agent: *\nCrawl-delay: 4\n"
    client, _, _ = make_client({
        f"{API}/robots.txt": [FakeResponse(200, rules, {"content-type": "text/plain"})],
        f"{API}/stats": [FakeResponse(200)],
    })
    client.get(f"{API}/stats")
    assert client.throttle.min_interval == 4.0


def test_unreachable_robots_is_treated_as_disallow():
    client, _, _ = make_client({f"{API}/robots.txt": [FakeResponse(500)]}, max_retries=0)
    with pytest.raises(StopScraping):
        client.get(f"{API}/stats")


def test_challenge_detection():
    assert is_challenge(FakeResponse(403, b"<html>", {"content-type": "text/html"}))
    assert is_challenge(FakeResponse(200, b"<html>", {"cf-mitigated": "challenge", "content-type": "text/html"}))
    assert not is_challenge(FakeResponse(200, b"{}", {"content-type": "application/json"}))


def test_challenge_stops_without_retry():
    client, session, _ = make_client({
        f"{API}/robots.txt": [FakeResponse(404)],
        f"{API}/stats": [FakeResponse(403, b"<html>Just a moment</html>", {"content-type": "text/html", "cf-mitigated": "challenge"})],
    })
    with pytest.raises(StopScraping, match="challenge"):
        client.get(f"{API}/stats")
    assert len(session.calls) == 2


def test_forbidden_json_stops():
    client, _, _ = make_client({
        f"{API}/robots.txt": [FakeResponse(404)],
        f"{API}/stats": [FakeResponse(403, b'{"error": "Forbidden"}')],
    })
    with pytest.raises(StopScraping, match="HTTP 403"):
        client.get(f"{API}/stats")


def test_rate_limit_honours_retry_after_then_succeeds():
    client, session, clock = make_client({
        f"{API}/robots.txt": [FakeResponse(404)],
        f"{API}/stats": [
            FakeResponse(429, b"{}", {"content-type": "application/json", "retry-after": "12"}),
            FakeResponse(200, b'{"ok": true}'),
        ],
    })
    response = client.get(f"{API}/stats")
    assert response.status_code == 200
    assert 12 in clock.sleeps
    assert len(session.calls) == 3


def test_retries_are_bounded():
    client, session, _ = make_client({
        f"{API}/robots.txt": [FakeResponse(404)],
        f"{API}/stats": [FakeResponse(503, b"{}")],
    })
    with pytest.raises(StopScraping, match="after 3 attempts"):
        client.get(f"{API}/stats")
    assert len(session.calls) == 4
