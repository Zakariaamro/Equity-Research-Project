"""CLI entry point and orchestration.

This module imports everything; nothing imports it. All `print()` calls in
the project belong here -- library modules only log.
"""

from __future__ import annotations

import argparse
import logging
import shutil
import sqlite3
import sys

from edgar import config, db, fetch, monitor, section_store, sections
from edgar.edgar_client import EdgarClient
from edgar.monitor import DiscoveredFiling

logger = logging.getLogger(__name__)


def _setup_logging() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        stream=sys.stdout,
    )


def _validate_ticker(ticker: str) -> None:
    known = {c.ticker for c in config.WATCHLIST}
    if ticker not in known:
        raise SystemExit(f"Unknown ticker {ticker!r}. Watchlist: {sorted(known)}")


def _print_row(*columns: str) -> None:
    print("  ".join(f"{c:<20}" for c in columns))


def _print_discovered_table(filings: list[DiscoveredFiling], status_label: str) -> None:
    if not filings:
        print("(none)")
        return
    _print_row("TICKER", "FORM", "FILING DATE", "PERIOD END", "ACCESSION", "STATUS")
    for f in filings:
        _print_row(
            f.ticker, f.form_type, f.filing_date, f.period_end or "-", f.accession_no, status_label
        )


def _print_filings_table(conn: sqlite3.Connection, accession_nos: list[str]) -> None:
    if not accession_nos:
        print("(none)")
        return
    placeholders = ",".join("?" for _ in accession_nos)
    rows = conn.execute(
        f"""
        SELECT c.ticker, f.form_type, f.filing_date, f.period_end, f.accession_no, f.status
        FROM filings f JOIN companies c ON c.cik = f.cik
        WHERE f.accession_no IN ({placeholders})
        ORDER BY f.filing_date
        """,
        accession_nos,
    ).fetchall()
    _print_row("TICKER", "FORM", "FILING DATE", "PERIOD END", "ACCESSION", "STATUS")
    for row in rows:
        _print_row(
            row["ticker"],
            row["form_type"],
            row["filing_date"],
            row["period_end"] or "-",
            row["accession_no"],
            row["status"],
        )


def cmd_init_db(args: argparse.Namespace) -> None:
    db.init_db()
    print(f"Database initialized at {config.DB_PATH}")


def cmd_discover(args: argparse.Namespace) -> None:
    tickers = None
    if args.ticker:
        _validate_ticker(args.ticker)
        tickers = [args.ticker]

    conn = db.get_connection()
    try:
        client = EdgarClient()
        discovered = monitor.find_new_filings(
            conn, client, tickers=tickers, limit=args.limit, dry_run=args.dry_run
        )

        if args.dry_run:
            print(f"[dry-run] would discover and fetch {len(discovered)} filing(s):")
            _print_discovered_table(discovered, "would-fetch")
            return

        print(f"Discovered {len(discovered)} new filing(s).")
        fetch_results = fetch.fetch_pending(conn, client, tickers=tickers, limit=args.limit)
        print(f"Fetched {len(fetch_results)} filing(s):")
        _print_filings_table(conn, [r["accession_no"] for r in fetch_results])
    finally:
        conn.close()


def cmd_extract(args: argparse.Namespace) -> None:
    tickers = None
    if args.ticker:
        _validate_ticker(args.ticker)
        tickers = [args.ticker]

    conn = db.get_connection()
    try:
        client = EdgarClient()
        results = sections.extract_pending(
            conn,
            client,
            tickers=tickers,
            accession=args.accession,
            limit=args.limit,
            force=args.force,
        )
        accession_nos = [r["accession_no"] for r in results]
        failures = sum(1 for r in results if r["status"] == "failed")

        written = 0
        if accession_nos:
            placeholders = ",".join("?" for _ in accession_nos)
            written = conn.execute(
                f"SELECT COUNT(*) AS n FROM sections WHERE accession_no IN ({placeholders})",
                accession_nos,
            ).fetchone()["n"]

        print(
            f"Processed {len(results)} filing(s): {written} section row(s) on file, "
            f"{failures} failure(s)."
        )
        _print_filings_table(conn, accession_nos)
    finally:
        conn.close()


def cmd_backfill_manifests(args: argparse.Namespace) -> None:
    conn = db.get_connection()
    try:
        client = EdgarClient()
        results = fetch.backfill_manifests(conn, client)
        written = sum(1 for r in results if r["status"] == "written")
        failed = sum(1 for r in results if r["status"] == "failed")
        print(f"Backfilled manifests for {len(results)} filing(s): {written} written, {failed} failed.")
    finally:
        conn.close()


def cmd_migrate_sections(args: argparse.Namespace) -> None:
    conn = db.get_connection()
    try:
        if not args.dry_run and section_store.needs_migration(conn):
            if not config.DB_PATH.exists():
                raise SystemExit(f"No database at {config.DB_PATH}")
            backup_path = config.DB_PATH.with_name(config.DB_PATH.name + config.DB_BACKUP_SUFFIX)
            shutil.copy2(config.DB_PATH, backup_path)
            print(f"Backed up {config.DB_PATH} -> {backup_path}")

        try:
            report = section_store.migrate_sections(conn, dry_run=args.dry_run)
        except (section_store.SqliteTooOldError, section_store.MigrationAbortedError) as exc:
            raise SystemExit(str(exc)) from exc

        print(report.summary())
    finally:
        conn.close()


def cmd_status(args: argparse.Namespace) -> None:
    conn = db.get_connection()
    try:
        rows = conn.execute(
            """
            SELECT c.ticker, f.form_type, f.status, COUNT(*) AS n
            FROM filings f JOIN companies c ON c.cik = f.cik
            GROUP BY c.ticker, f.form_type, f.status
            ORDER BY c.ticker, f.form_type, f.status
            """
        ).fetchall()
        if not rows:
            print("No filings recorded yet.")
            return
        _print_row("TICKER", "FORM", "STATUS", "COUNT")
        for row in rows:
            _print_row(row["ticker"], row["form_type"], row["status"], str(row["n"]))
    finally:
        conn.close()


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="python -m edgar.pipeline")
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("init-db", help="Create the database and seed the watchlist")

    p_discover = sub.add_parser("discover", help="Discover and fetch new filings")
    p_discover.add_argument("--ticker", help="Restrict to one watchlist ticker")
    p_discover.add_argument("--limit", type=int, default=None, help="Cap the number of filings")
    p_discover.add_argument(
        "--dry-run", action="store_true", help="Report what would happen without writing"
    )

    p_extract = sub.add_parser(
        "extract", help="Extract statements, notes, and policies into sections"
    )
    p_extract.add_argument("--ticker", help="Restrict to one watchlist ticker")
    p_extract.add_argument("--accession", help="Extract a single filing by accession number")
    p_extract.add_argument("--limit", type=int, default=None, help="Cap the number of filings")
    p_extract.add_argument(
        "--force", action="store_true", help="Re-extract filings already marked sectioned"
    )

    sub.add_parser(
        "backfill-manifests", help="Generate manifest.json for already-archived filings"
    )

    p_migrate = sub.add_parser(
        "migrate-sections",
        help="One-time: move sections.text to content-addressed storage, drop the column",
    )
    p_migrate.add_argument(
        "--dry-run",
        action="store_true",
        help="Report what would be written and verified, without writing files or touching the schema",
    )

    sub.add_parser("status", help="Summarize filings by company, form, and status")

    return parser


def main(argv: list[str] | None = None) -> int:
    _setup_logging()
    parser = build_parser()
    args = parser.parse_args(argv)

    if args.command == "init-db":
        cmd_init_db(args)
    elif args.command == "discover":
        cmd_discover(args)
    elif args.command == "extract":
        cmd_extract(args)
    elif args.command == "backfill-manifests":
        cmd_backfill_manifests(args)
    elif args.command == "migrate-sections":
        cmd_migrate_sections(args)
    elif args.command == "status":
        cmd_status(args)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
