"""Download and permanently archive raw filings.

Archive policy per ARCHITECTURE.md §4.1: 10-K/10-Q archive the primary
document plus FilingSummary.xml; 8-K archives every document listed in the
filing index (Exhibit 99.1 is the entire reason 8-Ks are tracked).

Also writes a `manifest.json` per filing (SPEC-002 R1): the complete set of
documents SEC declares for the filing, each annotated with its SEC-declared
type where one exists. Two different SEC resources are merged to build it
(see ARCHITECTURE.md §3.6) -- index.json for the complete file list,
{accession}-index.html's "Document Format Files" table for authoritative
types. Manifest generation degrades gracefully: it never blocks or fails
ingestion of the underlying filing.
"""

from __future__ import annotations

import gzip
import json
import logging
import re
import sqlite3
from pathlib import Path

from bs4 import BeautifulSoup

from edgar import config
from edgar.edgar_client import EdgarClient, EdgarError, EdgarNotFoundError

logger = logging.getLogger(__name__)


def _archive_dir(cik: str, accession_no: str) -> Path:
    return config.RAW_ARCHIVE_DIR / cik / accession_no


def _filenames_to_archive(
    form_type: str, primary_doc: str | None, all_filenames: list[str]
) -> list[str]:
    if form_type == config.EIGHTK_FORM_TYPE:
        return list(all_filenames)

    filenames = []
    if primary_doc:
        filenames.append(primary_doc)
    filenames.append(config.FILING_SUMMARY_FILENAME)
    return filenames


def _all_declared_filenames(client: EdgarClient, cik: str, accession_no: str) -> list[str]:
    """The complete file list for a filing, from index.json."""
    index = client.get_filing_index(cik, accession_no)
    return [item["name"] for item in index if item.get("name")]


def _parse_document_format_table(html: bytes) -> dict[str, tuple[str, str]]:
    """Parse the SEC-declared {filename: (type, description)} map from a
    filing's `-index.html` "Document Format Files" table.

    Best-effort: returns {} if the expected structure isn't found. Manifest
    generation must degrade gracefully rather than raise.
    """
    soup = BeautifulSoup(html, "html.parser")
    table = None
    for candidate in soup.find_all("table", class_="tableFile"):
        if "Document Format Files" in candidate.get("summary", ""):
            table = candidate
            break
    if table is None:
        return {}

    result: dict[str, tuple[str, str]] = {}
    for row in table.find_all("tr"):
        cells = row.find_all("td")
        if len(cells) < 4:
            continue  # header row or malformed row
        description = cells[1].get_text(strip=True)
        doc_type = cells[3].get_text(strip=True)
        link = cells[2].find("a")
        filename = (link.get_text(strip=True) if link else cells[2].get_text(strip=True))
        if filename:
            result[filename] = (doc_type, description)
    return result


def _get_document_types(
    client: EdgarClient, cik: str, accession_no: str
) -> dict[str, tuple[str, str]]:
    """Best-effort {filename: (type, description)}. Never raises."""
    index_filename = f"{accession_no}{config.FILING_INDEX_HTML_SUFFIX}"
    try:
        html = client.get_archive_file(cik, accession_no, index_filename)
    except EdgarError as exc:
        logger.warning(
            "Could not fetch index page for %s to determine document types: %s",
            accession_no,
            exc,
        )
        return {}

    try:
        return _parse_document_format_table(html)
    except Exception:
        logger.warning(
            "Could not parse document type table for %s; manifest will have empty types",
            accession_no,
        )
        return {}


def _build_manifest(
    cik: str,
    accession_no: str,
    form_type: str,
    all_filenames: list[str],
    archived_filenames: list[str],
    document_types: dict[str, tuple[str, str]],
) -> dict:
    archived_set = set(archived_filenames)
    documents = []
    for filename in all_filenames:
        doc_type, description = document_types.get(filename, ("", ""))
        documents.append(
            {
                "filename": filename,
                "type": doc_type,
                "description": description,
                "archived": filename in archived_set,
            }
        )
    return {
        "accession_no": accession_no,
        "cik": cik,
        "form_type": form_type,
        "documents": documents,
    }


def _write_manifest(archive_dir: Path, manifest: dict) -> None:
    path = archive_dir / config.MANIFEST_FILENAME
    path.write_text(json.dumps(manifest, indent=2))


def _normalise_doc_type(doc_type: str) -> str:
    """Strip whitespace, uppercase, and collapse repeated dots.

    SEC's own filing-index metadata occasionally contains data-entry typos
    -- confirmed on MU accession 0000723125-19-000172, whose Exhibit 99.1
    is declared as "EX-99..1" (double dot). Normalise before comparison
    rather than matching the raw string exactly.
    """
    return re.sub(r"\.{2,}", ".", doc_type.strip().upper())


def _has_exhibit_991(
    document_types: dict[str, tuple[str, str]], accession_no: str
) -> bool | None:
    """True/False if determinable from document_types; None if the type
    lookup itself was unavailable (so no claim can be made either way)."""
    if not document_types:
        return None
    found = False
    for filename, (doc_type, _description) in document_types.items():
        if not doc_type:
            continue
        normalised = _normalise_doc_type(doc_type)
        if normalised != doc_type:
            logger.warning(
                "Normalising SEC-declared type %r to %r for %s in %s -- "
                "source data quality issue, not a parsing bug",
                doc_type,
                normalised,
                filename,
                accession_no,
            )
        if normalised == config.EXHIBIT_991_TYPE:
            found = True
    return found


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
        all_filenames = _all_declared_filenames(client, cik, accession_no)
    except EdgarError as exc:
        # For 8-Ks the declared file list IS what we archive -- can't proceed
        # without it. For 10-K/10-Q we already know what to download; this
        # list only enriches the manifest, so degrade instead of failing.
        if form_type == config.EIGHTK_FORM_TYPE:
            _mark_failed(conn, accession_no, str(exc))
            return "failed"
        logger.warning(
            "Could not list declared documents for %s (%s); manifest will be incomplete",
            accession_no,
            exc,
        )
        all_filenames = []

    filenames_to_download = _filenames_to_archive(form_type, primary_doc, all_filenames)

    archive_dir.mkdir(parents=True, exist_ok=True)
    archived: list[str] = []
    for filename in filenames_to_download:
        try:
            content = client.get_archive_file(cik, accession_no, filename)
        except EdgarNotFoundError:
            if filename == config.FILING_SUMMARY_FILENAME:
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

    document_types = _get_document_types(client, cik, accession_no)

    if form_type == config.EIGHTK_FORM_TYPE and items and config.EIGHTK_REQUIRED_ITEM in items.split(","):
        has_ex991 = _has_exhibit_991(document_types, accession_no)
        if has_ex991 is False:
            logger.warning(
                "8-K %s carries Item %s but no document of type %s was declared "
                "in the filing index",
                accession_no,
                config.EIGHTK_REQUIRED_ITEM,
                config.EXHIBIT_991_TYPE,
            )
        elif has_ex991 is None:
            logger.info(
                "Could not verify Exhibit 99.1 presence for %s -- document type "
                "lookup unavailable",
                accession_no,
            )

    manifest_filenames = sorted(set(all_filenames) | set(archived))
    manifest = _build_manifest(
        cik, accession_no, form_type, manifest_filenames, archived, document_types
    )
    _write_manifest(archive_dir, manifest)

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


def _archived_filenames_on_disk(archive_dir: Path) -> set[str]:
    return {p.name[: -len(".gz")] for p in archive_dir.glob("*.gz")}


def backfill_manifest(conn: sqlite3.Connection, client: EdgarClient, accession_no: str) -> str:
    """Generate manifest.json for an already-archived filing, without
    re-downloading its content. Returns 'written' or 'failed'.

    Never raises: any failure is logged and reported as 'failed' so a batch
    backfill run is not interrupted by one bad filing.
    """
    row = conn.execute(
        "SELECT cik, form_type, raw_path FROM filings WHERE accession_no = ?",
        (accession_no,),
    ).fetchone()
    if row is None or not row["raw_path"]:
        logger.warning("No archive on disk for %s, skipping manifest backfill", accession_no)
        return "failed"

    cik = row["cik"]
    form_type = row["form_type"]
    archive_dir = Path(row["raw_path"])
    archived = _archived_filenames_on_disk(archive_dir)

    try:
        all_filenames = set(_all_declared_filenames(client, cik, accession_no))
    except EdgarError as exc:
        logger.warning(
            "Could not list declared documents for %s (%s); manifest limited to "
            "what's on disk",
            accession_no,
            exc,
        )
        all_filenames = set()

    document_types = _get_document_types(client, cik, accession_no)

    manifest_filenames = sorted(all_filenames | archived)
    manifest = _build_manifest(
        cik, accession_no, form_type, manifest_filenames, sorted(archived), document_types
    )
    try:
        _write_manifest(archive_dir, manifest)
    except OSError as exc:
        logger.warning("Could not write manifest for %s: %s", accession_no, exc)
        return "failed"
    return "written"


def backfill_manifests(conn: sqlite3.Connection, client: EdgarClient) -> list[dict[str, str]]:
    """Generate manifest.json for every already-archived filing."""
    rows = conn.execute(
        "SELECT accession_no FROM filings WHERE raw_path IS NOT NULL ORDER BY accession_no"
    ).fetchall()
    results: list[dict[str, str]] = []
    for row in rows:
        accession_no = row["accession_no"]
        try:
            status = backfill_manifest(conn, client, accession_no)
        except Exception:
            logger.exception("Unexpected error backfilling manifest for %s", accession_no)
            status = "failed"
        results.append({"accession_no": accession_no, "status": status})
    return results
