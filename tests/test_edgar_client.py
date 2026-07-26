from __future__ import annotations

import pytest
import requests

from edgar.edgar_client import EdgarClient, EdgarNotFoundError, EdgarRequestError, RateLimiter


class FakeClock:
    def __init__(self) -> None:
        self.now = 0.0
        self.sleeps: list[float] = []

    def time(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)
        self.now += seconds


class FakeResponse:
    def __init__(self, status_code: int = 200, json_data=None, content: bytes = b"") -> None:
        self.status_code = status_code
        self._json = json_data
        self.content = content

    @property
    def ok(self) -> bool:
        return self.status_code < 400

    def json(self):
        return self._json


class FakeSession:
    def __init__(self, responses) -> None:
        self._responses = list(responses)
        self.calls = 0

    def get(self, url, headers=None, timeout=None):
        self.calls += 1
        item = self._responses.pop(0)
        if isinstance(item, Exception):
            raise item
        return item


def make_client(session, **kwargs):
    clock = FakeClock()
    client = EdgarClient(
        user_agent="Test test@example.com",
        session=session,
        time_func=clock.time,
        sleep_func=clock.sleep,
        max_retries=3,
        backoff_base_seconds=0.01,
        backoff_max_seconds=1.0,
        timeout_seconds=5.0,
        **kwargs,
    )
    return client, clock


# ---- RateLimiter (injectable clock, no real sleeping) ----


def test_rate_limiter_enforces_max_rate():
    clock = FakeClock()
    limiter = RateLimiter(max_per_sec=8, time_func=clock.time, sleep_func=clock.sleep)
    for _ in range(16):
        limiter.acquire()
    assert clock.now >= 15 * (1 / 8) - 1e-9


def test_rate_limiter_skips_sleep_when_already_spaced_out():
    clock = FakeClock()
    limiter = RateLimiter(max_per_sec=8, time_func=clock.time, sleep_func=clock.sleep)
    limiter.acquire()
    clock.now += 1.0
    limiter.acquire()
    assert clock.sleeps == []


def test_rate_limiter_rejects_non_positive_rate():
    with pytest.raises(ValueError):
        RateLimiter(max_per_sec=0)


# ---- User-Agent ----


def test_missing_user_agent_fails_fast(monkeypatch):
    monkeypatch.delenv("SEC_USER_AGENT", raising=False)
    with pytest.raises(RuntimeError):
        EdgarClient()


# ---- Requests, retries, typed exceptions ----


def test_get_submissions_returns_parsed_json():
    session = FakeSession([FakeResponse(200, json_data={"cik": "1018724"})])
    client, _ = make_client(session)
    assert client.get_submissions("0001018724") == {"cik": "1018724"}
    assert session.calls == 1


def test_retries_on_5xx_then_succeeds():
    session = FakeSession(
        [FakeResponse(503), FakeResponse(500), FakeResponse(200, json_data={"ok": True})]
    )
    client, clock = make_client(session)
    assert client.get_submissions("x") == {"ok": True}
    assert session.calls == 3
    # Two failed attempts each trigger a backoff sleep; the rate limiter may
    # add its own pacing sleeps on top -- at least the two backoffs happened.
    assert len(clock.sleeps) >= 2


def test_retries_on_429():
    session = FakeSession([FakeResponse(429), FakeResponse(200, json_data={"ok": True})])
    client, _ = make_client(session)
    assert client.get_submissions("x") == {"ok": True}
    assert session.calls == 2


def test_retries_on_connection_error():
    session = FakeSession(
        [requests.ConnectionError("boom"), FakeResponse(200, json_data={"ok": True})]
    )
    client, _ = make_client(session)
    assert client.get_submissions("x") == {"ok": True}


def test_retries_on_timeout():
    session = FakeSession([requests.Timeout("slow"), FakeResponse(200, json_data={"ok": True})])
    client, _ = make_client(session)
    assert client.get_submissions("x") == {"ok": True}


def test_does_not_retry_404():
    session = FakeSession([FakeResponse(404)])
    client, _ = make_client(session)
    with pytest.raises(EdgarNotFoundError):
        client.get_submissions("x")
    assert session.calls == 1


def test_raises_typed_error_after_exhausting_retries():
    session = FakeSession([FakeResponse(503)] * 4)  # initial + 3 retries, all fail
    client, _ = make_client(session)
    with pytest.raises(EdgarRequestError):
        client.get_submissions("x")
    assert session.calls == 4


def test_get_archive_file_returns_bytes():
    session = FakeSession([FakeResponse(200, content=b"hello")])
    client, _ = make_client(session)
    result = client.get_archive_file("0001018724", "0001018724-26-000004", "FilingSummary.xml")
    assert result == b"hello"


def test_get_filing_index_returns_document_list():
    payload = {"directory": {"item": [{"name": "a.htm"}, {"name": "b.htm"}]}}
    session = FakeSession([FakeResponse(200, json_data=payload)])
    client, _ = make_client(session)
    result = client.get_filing_index("0001018724", "0001018724-26-000004")
    assert result == [{"name": "a.htm"}, {"name": "b.htm"}]
