"""Download and permanently archive raw filings.

Archive policy per ARCHITECTURE.md §4.1: 10-K/10-Q archive the primary
document plus FilingSummary.xml; 8-K archives every document listed in the
filing index (Exhibit 99.1 is the entire reason 8-Ks are tracked).
"""

from __future__ import annotations

import gzip
import logging
import re
import sqlite3
from pathlib import Path

from edgar import config
from edgar.edgar_client import EdgarClient, EdgarError, EdgarNotFoundError

logger = logging.getLogger(__name__)

_EXHIBIT_991_RE = re.compile(r"ex-?99\.?1", re.IGNORECASE)


def _archive_dir(cik: str, accession_no: str) -> Path:
    return config.RAW_ARCHIVE_DIR / cik / accession_no


def _filenames_to_archive(
    client: EdgarClient, cik: str, accession_no: str, form_type: str, primary_doc: str | None
) -> list[str]:
    if form_type == config.EIGHTK_FORM_TYPE:
        index = client.get_filing_index(cik, accession_no)
        return [item["name"] for item in index if item.get("name")]

    filenames = []
    if primary_doc:
        filenames.append(primary_doc)
    filenames.append("FilingSummary.xml")
    return filenames


def _mark_failed(conn: sqlite3.Connection, accession_no: str, reason: str) -> None:
    logger.warning("Marking %s as failed: %s", accession_no, reason)
    conn.execute("UPDATE filings SET status = 'failed' WHERE accession_no = ?", (accession_no,))
    conn.commit()


def fetch_filing(
    conn: sqlite3.Connection,
    client: EdgarClient,
    accession_no: str,
    force: bool = False,
) -> str:
    """Download and archive one filing. Returns 'fetched', 'skipped', or 'failed'.

    Never raises for SEC-side failures (typed EdgarError subclasses) -- those
    are caught, logged, and recorded as a 'failed' status so one bad filing
    does not halt a run.
    """
    row = conn.execute(
        "SELECT * FROM filings WHERE accession_no = ?", (accession_no,)
    ).fetchone()
    if row is None:
        raise ValueError(f"No filings row for accession_no {accession_no!r}")

    cik = row["cik"]
    form_type = row["form_type"]
    primary_doc = row["primary_doc"]
    items = row["items"]

    archive_dir = _archive_dir(cik, accession_no)
    if archive_dir.exists() and any(archive_dir.iterdir()) and not force:
        logger.info("Archive already exists for %s, skipping", accession_no)
        return "skipped"

    try:
        filenames = _filenames_to_archive(client, cik, accession_no, form_type, primary_doc)
    except EdgarError as exc:
        _mark_failed(conn, accession_no, str(exc))
        return "failed"

    archive_dir.mkdir(parents=True, exist_ok=True)
    archived: list[str] = []
    for filename in filenames:
        try:
            content = client.get_archive_file(cik, accession_no, filename)
        except EdgarNotFoundError:
            if filename == "FilingSummary.xml":
                logger.info(
                    "FilingSummary.xml absent for %s (normal for most 8-Ks)", accession_no
                )
            else:
                logger.warning("Document %s missing for %s", filename, accession_no)
            continue
        except EdgarError as exc:
            logger.warning("Failed to fetch %s for %s: %s", filename, accession_no, exc)
            continue

        dest = archive_dir / f"{filename}.gz"
        dest.write_bytes(gzip.compress(content))
        archived.append(filename)

    if not archived:
        _mark_failed(conn, accession_no, "no documents could be archived")
        return "failed"

    if form_type == config.EIGHTK_FORM_TYPE and items and config.EIGHTK_REQUIRED_ITEM in items.split(","):
        if not any(_EXHIBIT_991_RE.search(fn) for fn in archived):
            logger.warning(
                "8-K %s carries Item %s but no Exhibit 99.1 was found in the filing index",
                accession_no,
                config.EIGHTK_REQUIRED_ITEM,
            )

    conn.execute(
        "UPDATE filings SET raw_path = ?, primary_doc = ?, status = 'fetched' "
        "WHERE accession_no = ?",
        (str(archive_dir), primary_doc, accession_no),
    )
    conn.commit()
    return "fetched"


def fetch_pending(
    conn: sqlite3.Connection,
    client: EdgarClient,
    tickers: list[str] | None = None,
    limit: int | None = None,
) -> list[dict[str, str]]:
    """Fetch every filing currently in status='discovered'.

    A filing that fails is recorded as 'failed' and does not stop the rest
    of the batch from being processed.
    """
    query = "SELECT accession_no FROM filings WHERE status = 'discovered'"
    params: list[object] = []

    if tickers is not None:
        ciks = [c.cik for c in config.WATCHLIST if c.ticker in tickers]
        placeholders = ",".join("?" for _ in ciks)
        query += f" AND cik IN ({placeholders})"
        params.extend(ciks)

    query += " ORDER BY filing_date"
    if limit is not None:
        query += " LIMIT ?"
        params.append(limit)

    rows = conn.execute(query, params).fetchall()
    results: list[dict[str, str]] = []
    for row in rows:
        accession_no = row["accession_no"]
        try:
            status = fetch_filing(conn, client, accession_no)
        except Exception:
            logger.exception("Unexpected error fetching %s", accession_no)
            _mark_failed(conn, accession_no, "unexpected error")
            status = "failed"
        results.append({"accession_no": accession_no, "status": status})
    return results
