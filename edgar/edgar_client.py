"""The sole point of contact with sec.gov.

Every SEC HTTP request in the project must go through EdgarClient. Rate
limiting and the User-Agent header live here and nowhere else, per
ARCHITECTURE.md §5.
"""

from __future__ import annotations

import logging
import time
from typing import Any, Callable

import requests

from edgar import config

logger = logging.getLogger(__name__)

SUBMISSIONS_URL = "https://data.sec.gov/submissions/CIK{cik}.json"
FILING_INDEX_URL = "https://www.sec.gov/Archives/edgar/data/{cik_no_zeros}/{accession_no_nodash}/index.json"
ARCHIVE_FILE_URL = (
    "https://www.sec.gov/Archives/edgar/data/{cik_no_zeros}/{accession_no_nodash}/{filename}"
)
COMPANYFACTS_URL = "https://data.sec.gov/api/xbrl/companyfacts/CIK{cik}.json"

# Transient failures worth retrying. 404 is deliberately excluded -- it means
# the resource does not exist, not that the request failed transiently.
RETRYABLE_STATUS_CODES = {429, 500, 502, 503, 504}


class EdgarError(Exception):
    """Base class for permanent failures talking to SEC."""


class EdgarNotFoundError(EdgarError):
    """Raised on HTTP 404. Never retried."""


class EdgarRequestError(EdgarError):
    """Raised when a request fails permanently, including after exhausting retries."""


def strip_accession_dashes(accession_no: str) -> str:
    return accession_no.replace("-", "")


def strip_cik_leading_zeros(cik: str) -> str:
    return str(int(cik))


class RateLimiter:
    """Enforces a global minimum interval between calls.

    time_func/sleep_func are injectable so tests can drive a fake clock
    instead of sleeping for real (SPEC-001 R4).
    """

    def __init__(
        self,
        max_per_sec: float,
        time_func: Callable[[], float] = time.monotonic,
        sleep_func: Callable[[float], None] = time.sleep,
    ) -> None:
        if max_per_sec <= 0:
            raise ValueError("max_per_sec must be positive")
        self._min_interval = 1.0 / max_per_sec
        self._time_func = time_func
        self._sleep_func = sleep_func
        self._next_allowed_at: float | None = None

    def acquire(self) -> None:
        now = self._time_func()
        if self._next_allowed_at is not None and now < self._next_allowed_at:
            self._sleep_func(self._next_allowed_at - now)
            now = self._next_allowed_at
        self._next_allowed_at = now + self._min_interval


class EdgarClient:
    """HTTP client for sec.gov: User-Agent, rate limiting, retries, typed errors."""

    def __init__(
        self,
        user_agent: str | None = None,
        rate_limit_per_sec: float | None = None,
        max_retries: int | None = None,
        backoff_base_seconds: float | None = None,
        backoff_max_seconds: float | None = None,
        timeout_seconds: float | None = None,
        session: requests.Session | None = None,
        time_func: Callable[[], float] = time.monotonic,
        sleep_func: Callable[[float], None] = time.sleep,
    ) -> None:
        # Resolved eagerly (and fails fast) rather than deferred to first request,
        # so a misconfigured environment never sends an anonymous request.
        self._user_agent = user_agent if user_agent is not None else config.get_sec_user_agent()
        self._max_retries = (
            max_retries if max_retries is not None else config.HTTP_MAX_RETRIES
        )
        self._backoff_base = (
            backoff_base_seconds
            if backoff_base_seconds is not None
            else config.HTTP_BACKOFF_BASE_SECONDS
        )
        self._backoff_max = (
            backoff_max_seconds if backoff_max_seconds is not None else config.HTTP_BACKOFF_MAX_SECONDS
        )
        self._timeout = timeout_seconds if timeout_seconds is not None else config.HTTP_TIMEOUT_SECONDS
        self._session = session if session is not None else requests.Session()
        self._sleep_func = sleep_func
        limit = rate_limit_per_sec if rate_limit_per_sec is not None else config.SEC_RATE_LIMIT_PER_SEC
        self._rate_limiter = RateLimiter(limit, time_func=time_func, sleep_func=sleep_func)

    def _sleep_backoff(self, attempt: int) -> None:
        delay = min(self._backoff_base * (2**attempt), self._backoff_max)
        self._sleep_func(delay)

    def _get(self, url: str) -> requests.Response:
        headers = {"User-Agent": self._user_agent}
        attempt = 0
        while True:
            self._rate_limiter.acquire()
            try:
                response = self._session.get(url, headers=headers, timeout=self._timeout)
            except (requests.Timeout, requests.ConnectionError) as exc:
                if attempt >= self._max_retries:
                    raise EdgarRequestError(
                        f"GET {url} failed after {attempt + 1} attempt(s): {exc}"
                    ) from exc
                logger.info("GET %s failed (%s), retrying (attempt %d)", url, exc, attempt + 1)
                self._sleep_backoff(attempt)
                attempt += 1
                continue

            if response.status_code == 404:
                raise EdgarNotFoundError(f"GET {url} returned 404")

            if response.status_code in RETRYABLE_STATUS_CODES:
                if attempt >= self._max_retries:
                    raise EdgarRequestError(
                        f"GET {url} returned {response.status_code} after "
                        f"{attempt + 1} attempt(s)"
                    )
                logger.info(
                    "GET %s returned %d, retrying (attempt %d)",
                    url,
                    response.status_code,
                    attempt + 1,
                )
                self._sleep_backoff(attempt)
                attempt += 1
                continue

            if not response.ok:
                raise EdgarRequestError(f"GET {url} returned {response.status_code}")

            return response

    def get_submissions(self, cik: str) -> dict[str, Any]:
        url = SUBMISSIONS_URL.format(cik=cik)
        return self._get(url).json()

    def get_filing_index(self, cik: str, accession_no: str) -> list[dict[str, Any]]:
        """Return the complete list of documents in a filing (name, size, ...).

        This is index.json. Its own `type` field is a display icon class
        (e.g. "text.gif"), NOT the SEC-declared document type (EX-99.1,
        10-K, GRAPHIC, ...) -- see ARCHITECTURE.md §3.6. For the real
        document type, fetch and parse f"{accession_no}-index.html" via
        get_archive_file() instead.
        """
        url = FILING_INDEX_URL.format(
            cik_no_zeros=strip_cik_leading_zeros(cik),
            accession_no_nodash=strip_accession_dashes(accession_no),
        )
        data = self._get(url).json()
        return data.get("directory", {}).get("item", [])

    def get_company_facts(self, cik: str) -> dict[str, Any]:
        """Fetch the full XBRL companyfacts response for a company.

        Several MB. Raises EdgarNotFoundError if the CIK has no XBRL facts
        (SPEC-004 edge case: typed error naming the company is the caller's
        job, since this layer only knows the CIK).
        """
        url = COMPANYFACTS_URL.format(cik=cik)
        return self._get(url).json()

    def get_archive_file(self, cik: str, accession_no: str, filename: str) -> bytes:
        url = ARCHIVE_FILE_URL.format(
            cik_no_zeros=strip_cik_leading_zeros(cik),
            accession_no_nodash=strip_accession_dashes(accession_no),
            filename=filename,
        )
        return self._get(url).content
