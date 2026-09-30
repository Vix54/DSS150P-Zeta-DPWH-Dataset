import time
from urllib.parse import urlsplit

import requests
from protego import Protego

RETRYABLE_STATUS = {429, 500, 502, 503, 504}


class StopScraping(Exception):
    pass


class Throttle:
    def __init__(self, min_interval, clock=time.monotonic, sleep=time.sleep):
        self.min_interval = float(min_interval)
        self.clock = clock
        self.sleep = sleep
        self.last_request = None

    def wait(self):
        if self.last_request is not None:
            remaining = self.min_interval - (self.clock() - self.last_request)
            if remaining > 0:
                self.sleep(remaining)
        self.last_request = self.clock()


def is_challenge(response):
    if response.headers.get("cf-mitigated", "").lower() == "challenge":
        return True
    content_type = response.headers.get("content-type", "").lower()
    return response.status_code in (403, 503) and "text/html" in content_type


def build_user_agent(product, contact):
    return f"{product} (+contact: {contact})"


class PoliteClient:
    def __init__(
        self,
        user_agent,
        request_delay_seconds,
        timeout_seconds,
        max_retries,
        backoff_seconds,
        stop_on_status,
        respect_robots_txt=True,
        session=None,
        clock=time.monotonic,
        sleep=time.sleep,
    ):
        self.user_agent = user_agent
        self.timeout_seconds = timeout_seconds
        self.max_retries = int(max_retries)
        self.backoff_seconds = float(backoff_seconds)
        self.stop_on_status = set(stop_on_status)
        self.respect_robots_txt = respect_robots_txt
        self.session = session or requests.Session()
        self.sleep = sleep
        self.throttle = Throttle(request_delay_seconds, clock=clock, sleep=sleep)
        self.robots = {}
        self.robots_log = {}
        self.request_count = 0

    def _headers(self):
        return {"User-Agent": self.user_agent, "Accept": "application/json"}

    def _request(self, url):
        for attempt in range(self.max_retries + 1):
            self.throttle.wait()
            self.request_count += 1
            try:
                response = self.session.get(url, headers=self._headers(), timeout=self.timeout_seconds)
            except requests.RequestException as exc:
                if attempt == self.max_retries:
                    raise StopScraping(f"network error on {url} after {attempt + 1} attempts: {exc}") from exc
                self.sleep(self.backoff_seconds * (2 ** attempt))
                continue
            if is_challenge(response):
                raise StopScraping(f"bot-protection challenge on {url} (HTTP {response.status_code}); stopping without retry")
            if response.status_code in self.stop_on_status:
                raise StopScraping(f"HTTP {response.status_code} on {url}; access refused, stopping")
            if response.status_code in RETRYABLE_STATUS:
                if attempt == self.max_retries:
                    raise StopScraping(f"HTTP {response.status_code} on {url} after {attempt + 1} attempts")
                self.sleep(self._retry_wait(response, attempt))
                continue
            return response
        raise StopScraping(f"request to {url} did not complete")

    def _retry_wait(self, response, attempt):
        retry_after = response.headers.get("retry-after", "")
        if retry_after.isdigit():
            return max(float(retry_after), self.backoff_seconds)
        return self.backoff_seconds * (2 ** attempt)

    def _robots_for(self, url):
        parts = urlsplit(url)
        origin = f"{parts.scheme}://{parts.netloc}"
        if origin in self.robots:
            return self.robots[origin]
        robots_url = f"{origin}/robots.txt"
        response = self._request(robots_url)
        if response.status_code == 200:
            parser = Protego.parse(response.text)
            delay = parser.crawl_delay(self.user_agent)
            if delay and delay > self.throttle.min_interval:
                self.throttle.min_interval = float(delay)
            self.robots_log[origin] = {"url": robots_url, "status": 200, "rules": "parsed"}
        elif 400 <= response.status_code < 500:
            parser = None
            self.robots_log[origin] = {"url": robots_url, "status": response.status_code, "rules": "none published, all paths allowed"}
        else:
            raise StopScraping(f"robots.txt at {robots_url} unreachable (HTTP {response.status_code}); treating as full disallow")
        self.robots[origin] = parser
        return parser

    def get(self, url):
        if self.respect_robots_txt:
            parser = self._robots_for(url)
            if parser is not None and not parser.can_fetch(url, self.user_agent):
                raise StopScraping(f"robots.txt disallows {url}")
        return self._request(url)
