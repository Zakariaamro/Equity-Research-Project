"""Fetch companyfacts and normalise configured concepts into xbrl_facts (SPEC-004).

Ingest only. Period classification, restatement selection, and concept
resolution to a canonical input all happen later, in metrics.py, at
computation time -- see ARCHITECTURE.md §6 (xbrl_facts note) and SPEC-004 R1a.
"""

from __future__ import annotations

import logging
import sqlite3
from datetime import date
from typing import Any

from edgar import config
from edgar.edgar_client import EdgarClient, EdgarNotFoundError

logger = logging.getLogger(__name__)


class XbrlIngestError(Exception):
    """Raised when a company's companyfacts cannot be fetched at all."""


def _duration_days(start: str | None, end: str) -> int | None:
    if start is None:
        return None
    return (date.fromisoformat(end) - date.fromisoformat(start)).days


def _write_fact(
    conn: sqlite3.Connection,
    cik: str,
    concept: str,
    unit: str,
    entry: dict[str, Any],
    known_accessions: set[str],
) -> bool:
    """Insert one fact if not already present. Returns True if a row was written.

    Idempotency keys on `filed_date`, not `accession_no` -- `accession_no` is
    NULL whenever the source accn isn't in `filings` (companyfacts references
    amendments and form types our monitor doesn't track), and SQLite's UNIQUE
    constraint treats every NULL as distinct from every other NULL, so relying
    on it here would insert a duplicate row on every re-ingest for exactly the
    facts we can't resolve an accession for. `filed_date` is always present
    and genuinely distinguishes one filing's report of a period from another's
    (i.e. a restatement), so it is the correct idempotency key.
    """
    start = entry.get("start")
    end = entry["end"]
    filed = entry.get("filed")
    accn = entry.get("accn")
    accession_no = accn if accn in known_accessions else None
    duration = _duration_days(start, end)

    existing = conn.execute(
        """
        SELECT id FROM xbrl_facts
        WHERE cik = ? AND concept = ? AND unit = ?
          AND period_start IS ? AND period_end = ? AND filed_date IS ?
        """,
        (cik, concept, unit, start, end, filed),
    ).fetchone()
    if existing is not None:
        return False

    conn.execute(
        """
        INSERT INTO xbrl_facts (
            cik, taxonomy, concept, unit, period_start, period_end,
            fiscal_year, fiscal_period, value, accession_no, form_type,
            duration_days, filed_date
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            cik,
            config.COMPANYFACTS_TAXONOMY,
            concept,
            unit,
            start,
            end,
            entry.get("fy"),
            entry.get("fp"),
            entry["val"],
            accession_no,
            entry.get("form"),
            duration,
            filed,
        ),
    )
    return True


def _extract_fiscal_labels(
    taxonomy_facts: dict[str, Any], known_accessions: set[str]
) -> dict[str, tuple[int | None, str | None]]:
    """accession_no -> (fiscal_year, fiscal_period), scanned across EVERY
    concept in the payload -- not just configured ones -- so coverage does
    not depend on which canonical inputs happen to be tagged in a given
    filing (SPEC-005 change 9).

    Verified live before this was written: every one of 61 real NVDA/MU
    accessions maps to exactly one consistent (fy, fp) pair across all of
    its own facts. If a future filing ever violates that (multiple distinct
    pairs for the same accn), it is left unresolved rather than guessed --
    the whole point of this column is to be an authoritative label, not an
    inferred one.
    """
    labels: dict[str, tuple[int | None, str | None]] = {}
    conflicting: set[str] = set()
    for concept_data in taxonomy_facts.values():
        for entries in concept_data.get("units", {}).values():
            for entry in entries:
                accn = entry.get("accn")
                if accn not in known_accessions or accn in conflicting:
                    continue
                fy, fp = entry.get("fy"), entry.get("fp")
                if fy is None or fp is None:
                    continue
                existing = labels.get(accn)
                if existing is None:
                    labels[accn] = (fy, fp)
                elif existing != (fy, fp):
                    logger.warning(
                        "Conflicting fiscal labels for accession %s: %s vs %s -- leaving unresolved",
                        accn, existing, (fy, fp),
                    )
                    del labels[accn]
                    conflicting.add(accn)
    return labels


def ingest_company(conn: sqlite3.Connection, client: EdgarClient, cik: str) -> dict[str, Any]:
    """Ingest every configured concept present for one company.

    Returns {"written_by_concept": {alias: n}, "unresolved": [canonical, ...]}.
    `unresolved` names canonical inputs where no alias has any data at all for
    this company, in the declared unit -- a signal the registry may need
    widening (SPEC-004 R2/R10), not an error.

    Also backfills filings.fiscal_year/fiscal_period (SPEC-005 change 9) for
    every 10-K/10-Q filing already known to `filings` -- NULL is left in
    place for 8-Ks (companyfacts has no entries for them) and for any
    accession this company's companyfacts payload doesn't mention.
    """
    try:
        payload = client.get_company_facts(cik)
    except EdgarNotFoundError as exc:
        raise XbrlIngestError(f"No XBRL companyfacts for CIK {cik!r}") from exc

    taxonomy_facts = payload.get("facts", {}).get(config.COMPANYFACTS_TAXONOMY, {})

    filing_rows = conn.execute("SELECT accession_no, form_type FROM filings").fetchall()
    known_accessions = {row["accession_no"] for row in filing_rows}
    fiscal_eligible = {
        row["accession_no"]
        for row in filing_rows
        if row["form_type"] in (config.TENK_FORM_TYPE, config.TENQ_FORM_TYPE)
    }

    written_by_concept: dict[str, int] = {}
    unresolved: list[str] = []

    for canonical, concept_input in config.CONCEPT_REGISTRY.items():
        any_resolved = False
        for alias in concept_input.aliases:
            concept_data = taxonomy_facts.get(alias)
            if not concept_data:
                continue
            entries = concept_data.get("units", {}).get(concept_input.unit)
            if not entries:
                continue
            any_resolved = True
            written = sum(
                1 for entry in entries if _write_fact(conn, cik, alias, concept_input.unit, entry, known_accessions)
            )
            written_by_concept[alias] = written_by_concept.get(alias, 0) + written
        if not any_resolved:
            unresolved.append(canonical)

    fiscal_labels = _extract_fiscal_labels(taxonomy_facts, known_accessions)
    for accn, (fy, fp) in fiscal_labels.items():
        if accn not in fiscal_eligible:
            continue
        conn.execute(
            "UPDATE filings SET fiscal_year = ?, fiscal_period = ? WHERE accession_no = ?", (fy, fp, accn)
        )

    conn.commit()
    return {"written_by_concept": written_by_concept, "unresolved": unresolved}


def ingest_xbrl(
    conn: sqlite3.Connection, client: EdgarClient, tickers: list[str] | None = None
) -> list[dict[str, Any]]:
    """Ingest configured concepts for the watchlist (or a subset). Idempotent."""
    companies = [c for c in config.WATCHLIST if tickers is None or c.ticker in tickers]
    results: list[dict[str, Any]] = []
    for company in companies:
        try:
            outcome = ingest_company(conn, client, company.cik)
        except XbrlIngestError as exc:
            logger.warning("%s", exc)
            results.append({"ticker": company.ticker, "cik": company.cik, "error": str(exc)})
            continue
        results.append({"ticker": company.ticker, "cik": company.cik, **outcome})
    return results
