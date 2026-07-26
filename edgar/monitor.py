"""Discover filings present at SEC but absent from the database.

Filters to config.TRACKED_FORMS before any further work, resolves the
Item 2.02 8-K filter with a fallback path, deduplicates on accession_no,
and writes new rows with status='discovered'.
"""

from __future__ import annotations

import logging
import re
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from edgar import config
from edgar.edgar_client import EdgarClient, EdgarNotFoundError

logger = logging.getLogger(__name__)

_ITEM_RE = re.compile(r"Item\s+(\d+\.\d+)", re.IGNORECASE)


@dataclass(frozen=True)
class DiscoveredFiling:
    accession_no: str
    cik: str
    ticker: str
    form_type: str
    filing_date: str
    period_end: str | None
    items: str | None
    primary_doc: str | None


def _existing_accession_numbers(conn: sqlite3.Connection) -> set[str]:
    rows = conn.execute("SELECT accession_no FROM filings").fetchall()
    return {row["accession_no"] for row in rows}


def _split_items(items: str) -> list[str]:
    """Split a comma-separated item list, tolerating stray whitespace (e.g. "9.01, 2.02")."""
    return [item.strip() for item in items.split(",") if item.strip()]


def _resolve_8k_items(
    client: EdgarClient, cik: str, accession_no: str, items_from_submissions: str
) -> str | None:
    """Resolve the item list for an 8-K.

    Order: (1) the submissions JSON's own `items` field, if non-empty;
    (2) fall back to the filing's human-readable index page, which lists
    items under an "Items" heading; (3) exclude if both are empty.

    Verified during implementation: the submissions JSON's `items` array
    IS populated for AMZN/NVDA/MU live filings (e.g. "2.02,9.01"), so the
    fallback in practice is rarely exercised -- kept per spec for filings
    where it is blank.
    """
    if items_from_submissions and items_from_submissions.strip():
        return ",".join(_split_items(items_from_submissions))

    index_filename = f"{accession_no}-index.html"
    try:
        raw = client.get_archive_file(cik, accession_no, index_filename)
    except EdgarNotFoundError:
        return None

    text = raw.decode("utf-8", errors="replace")
    matches = _ITEM_RE.findall(text)
    if not matches:
        return None
    # Preserve order, drop duplicates.
    seen: list[str] = []
    for m in matches:
        if m not in seen:
            seen.append(m)
    return ",".join(seen)


def _iter_recent_filings(submissions: dict[str, Any]):
    recent = submissions.get("filings", {}).get("recent", {})
    forms = recent.get("form", [])
    for i in range(len(forms)):
        yield {
            "form": forms[i],
            "accession_no": recent.get("accessionNumber", [])[i],
            "filing_date": recent.get("filingDate", [])[i],
            "period_end": (recent.get("reportDate", []) or [None] * len(forms))[i] or None,
            "items": (recent.get("items", []) or [""] * len(forms))[i],
            "primary_doc": (recent.get("primaryDocument", []) or [None] * len(forms))[i] or None,
        }


def find_new_filings(
    conn: sqlite3.Connection,
    client: EdgarClient,
    tickers: list[str] | None = None,
    limit: int | None = None,
    dry_run: bool = False,
) -> list[DiscoveredFiling]:
    """Discover, record, and return filings not yet in the `filings` table.

    Idempotent: filings already present in the database (by accession_no)
    are skipped, so running this twice in succession discovers nothing new
    the second time.

    `limit` caps how many newly discovered filings are recorded/returned.
    `dry_run` skips the database write entirely (preview only).
    """
    companies = [c for c in config.WATCHLIST if tickers is None or c.ticker in tickers]
    known = _existing_accession_numbers(conn)
    discovered: list[DiscoveredFiling] = []
    now = datetime.now(timezone.utc).isoformat()

    for company in companies:
        submissions = client.get_submissions(company.cik)
        for entry in _iter_recent_filings(submissions):
            if entry["form"] not in config.TRACKED_FORMS:
                continue
            if entry["accession_no"] in known:
                continue

            items: str | None = None
            if entry["form"] == config.EIGHTK_FORM_TYPE:
                items = _resolve_8k_items(
                    client, company.cik, entry["accession_no"], entry["items"]
                )
                if not items or config.EIGHTK_REQUIRED_ITEM not in _split_items(items):
                    continue

            filing = DiscoveredFiling(
                accession_no=entry["accession_no"],
                cik=company.cik,
                ticker=company.ticker,
                form_type=entry["form"],
                filing_date=entry["filing_date"],
                period_end=entry["period_end"],
                items=items,
                primary_doc=entry["primary_doc"],
            )
            discovered.append(filing)
            known.add(filing.accession_no)

    if limit is not None:
        discovered = discovered[:limit]

    if dry_run:
        return discovered

    for filing in discovered:
        conn.execute(
            """
            INSERT INTO filings (
                accession_no, cik, form_type, filing_date, period_end,
                items, primary_doc, raw_path, discovered_at, status
            ) VALUES (?, ?, ?, ?, ?, ?, ?, NULL, ?, 'discovered')
            """,
            (
                filing.accession_no,
                filing.cik,
                filing.form_type,
                filing.filing_date,
                filing.period_end,
                filing.items,
                filing.primary_doc,
                now,
            ),
        )
    conn.commit()

    return discovered
