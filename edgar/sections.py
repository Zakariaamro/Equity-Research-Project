"""Parse FilingSummary.xml, fetch R-files, and extract clean text into `sections`.

Statements, Notes, and Policies only, for 10-K and 10-Q filings. MD&A, Risk
Factors, and Exhibit 99.1 extraction are SPEC-003 -- not this module.
"""

from __future__ import annotations

import gzip
import logging
import sqlite3
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from pathlib import Path

from edgar import config, section_store
from edgar.edgar_client import EdgarClient, EdgarError, EdgarNotFoundError
from edgar.html_text import html_to_text

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class ReportRef:
    short_name: str
    category: str
    html_file_name: str
    position: int | None


def _read_filing_summary(raw_path: str) -> bytes | None:
    path = Path(raw_path) / f"{config.FILING_SUMMARY_FILENAME}.gz"
    if not path.exists():
        return None
    return gzip.decompress(path.read_bytes())


def _select_reports(filing_summary_xml: bytes) -> list[ReportRef]:
    """Select Statements/Notes/Policies reports from a parsed FilingSummary.xml.

    Cover, Tables, and Details are deliberately excluded -- Tables and
    Details are XBRL tagging detail that duplicates the parent note/statement.
    """
    root = ET.fromstring(filing_summary_xml)
    reports: list[ReportRef] = []
    for report in root.iter("Report"):
        category = report.findtext("MenuCategory") or ""
        if category not in config.SECTION_MENUCATEGORIES:
            continue
        short_name = report.findtext("ShortName") or ""
        html_file_name = report.findtext("HtmlFileName") or ""
        if not short_name or not html_file_name:
            continue
        position_text = report.findtext("Position")
        position = int(position_text) if position_text and position_text.isdigit() else None
        reports.append(ReportRef(short_name, category, html_file_name, position))
    return reports


def _write_section(conn: sqlite3.Connection, accession_no: str, report: ReportRef, text: str) -> None:
    """Write text to the content-addressed store and record only the hash.

    If a row for this (accession_no, category, short_name, source_file) already
    carries the same hash, the DB write is skipped entirely -- re-extracting
    identical text changes no row (SPEC-003 R5). Only a changed hash touches
    the table.
    """
    text_hash = section_store.write_section_text(text)

    existing = conn.execute(
        """
        SELECT text_hash FROM sections
        WHERE accession_no = ? AND category = ? AND short_name = ? AND source_file = ?
        """,
        (accession_no, report.category, report.short_name, report.html_file_name),
    ).fetchone()
    if existing is not None and existing["text_hash"] == text_hash:
        return

    conn.execute(
        """
        INSERT INTO sections (
            accession_no, category, short_name, source_file, position, text_hash
        ) VALUES (?, ?, ?, ?, ?, ?)
        ON CONFLICT(accession_no, category, short_name, source_file) DO UPDATE SET
            position = excluded.position,
            text_hash = excluded.text_hash
        """,
        (
            accession_no,
            report.category,
            report.short_name,
            report.html_file_name,
            report.position,
            text_hash,
        ),
    )


def extract_filing(conn: sqlite3.Connection, client: EdgarClient, accession_no: str) -> str:
    """Extract sections for one 10-K/10-Q. Returns 'sectioned', 'skipped', or 'failed'.

    'failed' or 'skipped' leave filings.status unchanged so the filing can be
    retried; status only advances to 'sectioned' once every selected report
    has a row written. R-files are fetched live and never archived to disk
    (SPEC-002 R3) -- once extracted, the R-file is never needed again.
    """
    row = conn.execute(
        "SELECT * FROM filings WHERE accession_no = ?", (accession_no,)
    ).fetchone()
    if row is None:
        raise ValueError(f"No filings row for accession_no {accession_no!r}")

    cik = row["cik"]
    form_type = row["form_type"]
    raw_path = row["raw_path"]

    if form_type not in config.SECTION_EXTRACTABLE_FORM_TYPES:
        logger.debug(
            "Skipping %s: form type %s is not extracted by this spec", accession_no, form_type
        )
        return "skipped"

    if not raw_path:
        logger.warning("No archive on disk for %s, skipping", accession_no)
        return "skipped"

    filing_summary_xml = _read_filing_summary(raw_path)
    if filing_summary_xml is None:
        logger.warning("FilingSummary.xml missing from archive for %s, skipping", accession_no)
        return "skipped"

    try:
        reports = _select_reports(filing_summary_xml)
    except ET.ParseError as exc:
        logger.warning("Could not parse FilingSummary.xml for %s: %s", accession_no, exc)
        return "failed"

    if not reports:
        logger.warning(
            "No Statements/Notes/Policies reports found in FilingSummary.xml for %s",
            accession_no,
        )
        return "failed"

    written = 0
    failures = 0
    for report in reports:
        try:
            html = client.get_archive_file(cik, accession_no, report.html_file_name)
        except EdgarNotFoundError:
            logger.warning(
                "R-file %s (%s) missing for %s",
                report.html_file_name,
                report.short_name,
                accession_no,
            )
            failures += 1
            continue
        except EdgarError as exc:
            logger.warning(
                "Failed to fetch %s for %s: %s", report.html_file_name, accession_no, exc
            )
            failures += 1
            continue

        text = html_to_text(html)
        if not text.strip():
            logger.warning(
                "Extracted empty text for %s / %s -- not writing a row",
                accession_no,
                report.short_name,
            )
            failures += 1
            continue

        _write_section(conn, accession_no, report, text)
        written += 1

    conn.commit()  # persist whatever succeeded, even on partial failure

    if failures > 0:
        logger.warning(
            "%s: %d/%d sections written, %d failed -- status left unchanged for retry",
            accession_no,
            written,
            len(reports),
            failures,
        )
        return "failed"

    conn.execute("UPDATE filings SET status = 'sectioned' WHERE accession_no = ?", (accession_no,))
    conn.commit()
    return "sectioned"


def extract_pending(
    conn: sqlite3.Connection,
    client: EdgarClient,
    tickers: list[str] | None = None,
    accession: str | None = None,
    limit: int | None = None,
    force: bool = False,
) -> list[dict[str, str]]:
    """Extract sections for filings with status='fetched'.

    `force` also re-processes filings already 'sectioned'. Guarded on
    status, so re-running with the same arguments and force=False processes
    nothing and issues no R-file fetches -- idempotent by construction.
    """
    statuses = ["fetched", "sectioned"] if force else ["fetched"]

    form_placeholders = ",".join("?" for _ in config.SECTION_EXTRACTABLE_FORM_TYPES)
    status_placeholders = ",".join("?" for _ in statuses)
    query = (
        f"SELECT accession_no FROM filings "
        f"WHERE form_type IN ({form_placeholders}) AND status IN ({status_placeholders})"
    )
    params: list[object] = list(config.SECTION_EXTRACTABLE_FORM_TYPES) + statuses

    if accession is not None:
        query += " AND accession_no = ?"
        params.append(accession)

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
            status = extract_filing(conn, client, accession_no)
        except Exception:
            logger.exception("Unexpected error extracting %s", accession_no)
            status = "failed"
        results.append({"accession_no": accession_no, "status": status})
    return results
