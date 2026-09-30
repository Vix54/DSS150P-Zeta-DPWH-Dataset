from requests.structures import CaseInsensitiveDict


class FakeResponse:
    def __init__(self, status_code=200, body=b"{}", headers=None):
        self.status_code = status_code
        self.content = body
        self.text = body.decode("utf-8")
        self.headers = CaseInsensitiveDict(headers or {"content-type": "application/json"})


class FakeSession:
    def __init__(self, routes):
        self.routes = {url: list(responses) for url, responses in routes.items()}
        self.calls = []

    def get(self, url, headers=None, timeout=None):
        self.calls.append((url, headers))
        queue = self.routes[url]
        return queue.pop(0) if len(queue) > 1 else queue[0]


class FakeClock:
    def __init__(self):
        self.now = 0.0
        self.sleeps = []

    def clock(self):
        return self.now

    def sleep(self, seconds):
        self.sleeps.append(seconds)
        self.now += seconds
