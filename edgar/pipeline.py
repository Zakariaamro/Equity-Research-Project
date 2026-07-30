"""CLI entry point and orchestration.

This module imports everything; nothing imports it. All `print()` calls in
the project belong here -- library modules only log.
"""

from __future__ import annotations

import argparse
import json
import logging
import shutil
import sqlite3
import sys

from edgar import analyze, brief, config, db, fetch, llm, metrics, monitor, observations, section_store, sections, validate, xbrl
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


def cmd_ingest_xbrl(args: argparse.Namespace) -> None:
    tickers = None
    if args.ticker:
        _validate_ticker(args.ticker)
        tickers = [args.ticker]

    conn = db.get_connection()
    try:
        client = EdgarClient()
        results = xbrl.ingest_xbrl(conn, client, tickers=tickers)
        for r in results:
            if "error" in r:
                print(f"{r['ticker']}: ERROR - {r['error']}")
                continue
            total_written = sum(r["written_by_concept"].values())
            print(f"{r['ticker']}: {total_written} fact(s) written across {len(r['written_by_concept'])} concept(s)")
            for concept, n in sorted(r["written_by_concept"].items()):
                print(f"    {concept}: {n}")
            if r["unresolved"]:
                print(f"    unresolved (no alias has any data): {', '.join(sorted(r['unresolved']))}")
    finally:
        conn.close()


def cmd_compute_metrics(args: argparse.Namespace) -> None:
    tickers = None
    if args.ticker:
        _validate_ticker(args.ticker)
        tickers = [args.ticker]
    metric_names = [args.metric] if args.metric else None

    conn = db.get_connection()
    try:
        results = metrics.compute_metrics(conn, tickers=tickers, metric_names=metric_names)
        computed = [r for r in results if r["value"] is not None]
        nulls = [r for r in results if r["value"] is None]
        print(f"{len(results)} metric row(s) written/updated: {len(computed)} computed, {len(nulls)} NULL.")
        if nulls:
            reason_counts: dict[str, int] = {}
            for r in nulls:
                reason = r["null_reason"] or "unknown"
                reason_counts[reason] = reason_counts.get(reason, 0) + 1
            print("  NULL reasons:")
            for reason, count in sorted(reason_counts.items(), key=lambda kv: -kv[1]):
                print(f"    {count:4d}  {reason}")
    finally:
        conn.close()


def cmd_backfill_readability(args: argparse.Namespace) -> None:
    conn = db.get_connection()
    try:
        result = sections.backfill_readability(conn, force=args.force)
        print(
            f"{result['updated']} section(s) updated, {result['missing_content']} missing content "
            f"({result['eligible']}/{result['total']} eligible)."
        )
    finally:
        conn.close()


def cmd_compute_observations(args: argparse.Namespace) -> None:
    tickers = None
    if args.ticker:
        _validate_ticker(args.ticker)
        tickers = [args.ticker]
    rule_names = [args.rule] if args.rule else None

    conn = db.get_connection()
    try:
        written = observations.compute_observations(conn, tickers=tickers, rule_names=rule_names)
        print(f"{len(written)} observation(s) written/updated.")
        by_rule: dict[str, int] = {}
        by_ticker: dict[str, int] = {}
        for w in written:
            by_rule[w["rule_name"]] = by_rule.get(w["rule_name"], 0) + 1
            by_ticker[w["ticker"]] = by_ticker.get(w["ticker"], 0) + 1
        if by_rule:
            print("  By rule:")
            for name, n in sorted(by_rule.items(), key=lambda kv: -kv[1]):
                print(f"    {n:4d}  {name}")
        if by_ticker:
            print("  By company:")
            for ticker, n in sorted(by_ticker.items()):
                print(f"    {n:4d}  {ticker}")
    finally:
        conn.close()


def cmd_validate(args: argparse.Namespace) -> None:
    tickers = None
    if args.ticker:
        _validate_ticker(args.ticker)
        tickers = [args.ticker]

    conn = db.get_connection()
    try:
        report = validate.run_validate(conn, tickers=tickers)
        print(validate.format_report(report))
        if report.hard_failure_count:
            raise SystemExit(1)
    finally:
        conn.close()


def _confirm_cost_tolerance(estimate: float) -> float:
    """SPEC-006A L4: how close --confirm-cost N must be to the dry-run
    estimate. Not specified by the spec ("a small tolerance") -- 1% of the
    estimate, floored at $0.01 (config.LLM_CONFIRM_COST_TOLERANCE_*)."""
    return max(config.LLM_CONFIRM_COST_TOLERANCE_FLOOR_USD, estimate * config.LLM_CONFIRM_COST_TOLERANCE_FRACTION)


def _print_cache_impact(stats: analyze.RunStats) -> None:
    """SPEC-006A L5: reported before every run, execute or dry-run alike --
    "before any run, report calls that will hit the cache, calls that are
    new..." """
    print(
        f"  cache: {stats.cache_hits_dry} hit(s), {stats.new_calls_dry} new call(s), "
        f"estimated cost of new calls: ${stats.estimated_cost_usd:.4f}"
    )
    if stats.version_bumped:
        print(
            f"  prompt version changed since last run (prior version(s): {', '.join(stats.prior_prompt_versions)}, "
            f"current: {config.SECTION_ANALYSIS_PROMPT_VERSION}): "
            f"{stats.invalidated_by_version_bump} previously-cached analysis(es) invalidated, "
            f"${stats.invalidated_cost_usd:.4f} to regenerate"
        )


def cmd_analyze_sections(args: argparse.Namespace) -> None:
    tickers = None
    if args.ticker:
        _validate_ticker(args.ticker)
        tickers = [args.ticker]

    conn = db.get_connection()
    try:
        if args.scheduled and args.sample is not None:
            llm.record_refusal(
                conn,
                "L7: --scheduled runs must never use --sample (a prompt-development-only bypass)",
                prompt_name=config.SECTION_ANALYSIS_PROMPT_NAME,
                prompt_version=config.SECTION_ANALYSIS_PROMPT_VERSION,
            )
            raise SystemExit("Refusing: --scheduled runs must never use --sample.")

        # SPEC-006A: always compute the dry-run pass first, execute or not --
        # it is where L5's cache-impact report and L4's confirmation number
        # both come from, and it makes zero calls / no spend either way.
        dry_stats = analyze.run_analysis(
            conn, tickers=tickers, accession=args.accession, sample=args.sample, seed=args.seed,
            limit=args.limit, execute=False, scheduled=args.scheduled,
        )
        print(f"[DRY RUN] {dry_stats.candidates} candidate section(s), {dry_stats.processed} selected.")
        if dry_stats.seed_used is not None:
            print(f"  sample seed: {dry_stats.seed_used}")
        _print_cache_impact(dry_stats)

        if not args.execute:
            return

        # L4: confirmation gate. Below LLM_CONFIRM_THRESHOLD_USD, no
        # confirmation is required at all -- the whole point is to make the
        # operator READ a number that matters, not force a ritual on every
        # $0.02 dev run.
        estimate = dry_stats.estimated_cost_usd
        if estimate > config.LLM_CONFIRM_THRESHOLD_USD:
            if args.confirm_cost is None:
                llm.record_refusal(
                    conn,
                    f"L4: estimated cost ${estimate:.4f} exceeds confirmation threshold "
                    f"${config.LLM_CONFIRM_THRESHOLD_USD:.2f} and no --confirm-cost was given",
                    prompt_name=config.SECTION_ANALYSIS_PROMPT_NAME,
                    prompt_version=config.SECTION_ANALYSIS_PROMPT_VERSION,
                )
                raise SystemExit(
                    f"Refusing: estimated cost ${estimate:.4f} exceeds the "
                    f"${config.LLM_CONFIRM_THRESHOLD_USD:.2f} confirmation threshold. "
                    f"Re-run with --confirm-cost {estimate:.4f} to proceed."
                )
            tolerance = _confirm_cost_tolerance(estimate)
            if abs(args.confirm_cost - estimate) > tolerance:
                llm.record_refusal(
                    conn,
                    f"L4: --confirm-cost {args.confirm_cost:.4f} does not match dry-run estimate "
                    f"${estimate:.4f} (tolerance ${tolerance:.4f})",
                    prompt_name=config.SECTION_ANALYSIS_PROMPT_NAME,
                    prompt_version=config.SECTION_ANALYSIS_PROMPT_VERSION,
                )
                raise SystemExit(
                    f"Refusing: --confirm-cost {args.confirm_cost:.4f} does not match the current "
                    f"dry-run estimate of ${estimate:.4f} (tolerance ${tolerance:.4f}). "
                    "Re-read the estimate above and pass it exactly."
                )

        # L5: cache-invalidation acknowledgement gate.
        if (
            dry_stats.invalidated_by_version_bump > config.LLM_CACHE_INVALIDATION_WARN
            and not args.acknowledge_cache_invalidation
        ):
            llm.record_refusal(
                conn,
                f"L5: prompt version bump invalidates {dry_stats.invalidated_by_version_bump} previously-cached "
                f"analysis(es) (> {config.LLM_CACHE_INVALIDATION_WARN}), costing "
                f"${dry_stats.invalidated_cost_usd:.4f} to regenerate, and --acknowledge-cache-invalidation "
                "was not given",
                prompt_name=config.SECTION_ANALYSIS_PROMPT_NAME,
                prompt_version=config.SECTION_ANALYSIS_PROMPT_VERSION,
            )
            raise SystemExit(
                f"Refusing: this run would invalidate {dry_stats.invalidated_by_version_bump} previously-cached "
                f"analyses (${dry_stats.invalidated_cost_usd:.4f} to regenerate). "
                "Re-run with --acknowledge-cache-invalidation to proceed."
            )

        # Built before any real API-touching work: if ANTHROPIC_API_KEY
        # (the project's own, EQUITY_RESEARCH_ANTHROPIC_API_KEY) is unset,
        # fail immediately naming the variable, never after work is done.
        client = llm.LLMClient()

        stats = analyze.run_analysis(
            conn,
            tickers=tickers,
            accession=args.accession,
            sample=args.sample,
            seed=args.seed,
            limit=args.limit,
            execute=True,
            client=client,
            max_run_cost_usd=args.max_run_cost,
            max_calls_per_run=args.max_calls,
            scheduled=args.scheduled,
        )

        print(f"[EXECUTE] {stats.candidates} candidate section(s), {stats.processed} processed.")
        print(
            f"  calls made: {stats.calls_made}, cache hits: {stats.cache_hits}, "
            f"refused: {stats.refused}, errors: {stats.errors}, truncated: {stats.truncated}, "
            f"skipped (oversized): {stats.skipped_oversized}"
        )
        print(
            f"  findings returned: {stats.findings_returned}, kept: {stats.findings_kept}, "
            f"discarded: {stats.findings_discarded}"
        )
        rate = stats.numeric_support_rate
        if rate is None:
            print("  numeric support: no numbers checked (no findings kept)")
        else:
            print(
                f"  numeric support: {stats.numeric_tokens_supported}/{stats.numeric_tokens_checked} "
                f"tokens found in source ({rate:.0%}) -- "
                f"in quote: {stats.numeric_tokens_supported_in_quote} ({stats.numeric_support_rate_in_quote:.0%}), "
                f"in note only: {stats.numeric_tokens_supported_in_note_only} "
                f"({stats.numeric_support_rate_in_note_only:.0%}), "
                f"derived-verified: {stats.numeric_tokens_derived_verified} "
                f"({stats.numeric_derived_verified_rate:.0%}); "
                f"{stats.findings_with_unsupported_numbers} finding(s) with at least one unsupported number "
                f"(warning only, not discarded)"
            )

        # L10: every paid command states this run's cost, lifetime spend,
        # and remaining budget on completion. No paid operation may
        # complete without stating what it cost.
        print(
            f"  this run's cost: ${stats.run_cost_usd:.4f} | "
            f"lifetime spend: ${stats.total_cost_usd:.4f} of ${config.LLM_BUDGET_USD:.2f} budget | "
            f"remaining: ${config.LLM_BUDGET_USD - stats.total_cost_usd:.4f}"
        )

        if stats.stopped_reason is not None:
            print(f"  STOPPED EARLY: {stats.stopped_reason}")
            raise SystemExit(1)
    finally:
        conn.close()


def cmd_spend(args: argparse.Namespace) -> None:
    conn = db.get_connection()
    try:
        summary = llm.spend_summary(conn)
        print(
            f"Total spent: ${summary['total_spent']:.4f} of ${summary['budget']:.2f} budget "
            f"(${summary['remaining']:.4f} remaining)"
        )
        if not summary["breakdown"]:
            print("  (no calls recorded yet)")
        else:
            print("  By prompt / model / status:")
            for row in summary["breakdown"]:
                print(
                    f"    {row['prompt_name'] or '-'} / {row['model']} / {row['status']}: "
                    f"{row['n']} call(s), ${row['cost']:.4f}"
                )
    finally:
        conn.close()


def _print_brief_cache_impact(stats: brief.BriefRunStats) -> None:
    """SPEC-006A L5, applied to briefs: reported before every run, execute
    or dry-run alike."""
    print(
        f"  cache: {stats.cache_hits_dry} hit(s), {stats.new_calls_dry} new call(s), "
        f"no observations/findings (no cost): {stats.empty_no_input_dry}, "
        f"estimated cost of new calls: ${stats.estimated_cost_usd:.4f}"
    )
    if stats.version_bumped:
        print(
            f"  prompt/verifier version changed since last run "
            f"(prior prompt version(s): {', '.join(stats.prior_prompt_versions) or '-'}, "
            f"current: {config.BRIEF_GENERATOR_PROMPT_VERSION}; "
            f"prior verifier version(s): {', '.join(stats.prior_verifier_versions) or '-'}, "
            f"current: {config.BRIEF_VERIFIER_VERSION}): "
            f"{stats.invalidated_by_version_bump} previously-cached brief(s) invalidated, "
            f"${stats.invalidated_cost_usd:.4f} to regenerate"
        )


def cmd_generate_briefs(args: argparse.Namespace) -> None:
    tickers = None
    if args.ticker:
        _validate_ticker(args.ticker)
        tickers = [args.ticker]

    conn = db.get_connection()
    try:
        # SPEC-006A: dry-run pass first, execute or not -- makes zero calls,
        # no spend, either way.
        dry_stats = brief.run_brief_generation(conn, tickers=tickers, accession=args.accession, execute=False)
        print(f"[DRY RUN] {dry_stats.candidates} candidate filing(s), {dry_stats.processed} selected.")
        _print_brief_cache_impact(dry_stats)

        if not args.execute:
            return

        # L4: confirmation gate, unchanged from analyze-sections.
        estimate = dry_stats.estimated_cost_usd
        if estimate > config.LLM_CONFIRM_THRESHOLD_USD:
            if args.confirm_cost is None:
                llm.record_refusal(
                    conn,
                    f"L4: estimated cost ${estimate:.4f} exceeds confirmation threshold "
                    f"${config.LLM_CONFIRM_THRESHOLD_USD:.2f} and no --confirm-cost was given",
                    prompt_name=config.BRIEF_GENERATOR_PROMPT_NAME,
                    prompt_version=config.BRIEF_GENERATOR_PROMPT_VERSION,
                )
                raise SystemExit(
                    f"Refusing: estimated cost ${estimate:.4f} exceeds the "
                    f"${config.LLM_CONFIRM_THRESHOLD_USD:.2f} confirmation threshold. "
                    f"Re-run with --confirm-cost {estimate:.4f} to proceed."
                )
            tolerance = _confirm_cost_tolerance(estimate)
            if abs(args.confirm_cost - estimate) > tolerance:
                llm.record_refusal(
                    conn,
                    f"L4: --confirm-cost {args.confirm_cost:.4f} does not match dry-run estimate "
                    f"${estimate:.4f} (tolerance ${tolerance:.4f})",
                    prompt_name=config.BRIEF_GENERATOR_PROMPT_NAME,
                    prompt_version=config.BRIEF_GENERATOR_PROMPT_VERSION,
                )
                raise SystemExit(
                    f"Refusing: --confirm-cost {args.confirm_cost:.4f} does not match the current "
                    f"dry-run estimate of ${estimate:.4f} (tolerance ${tolerance:.4f}). "
                    "Re-read the estimate above and pass it exactly."
                )

        # L5: cache-invalidation acknowledgement gate.
        if (
            dry_stats.invalidated_by_version_bump > config.LLM_CACHE_INVALIDATION_WARN
            and not args.acknowledge_cache_invalidation
        ):
            llm.record_refusal(
                conn,
                f"L5: prompt/verifier version bump invalidates {dry_stats.invalidated_by_version_bump} "
                f"previously-cached brief(s) (> {config.LLM_CACHE_INVALIDATION_WARN}), costing "
                f"${dry_stats.invalidated_cost_usd:.4f} to regenerate, and --acknowledge-cache-invalidation "
                "was not given",
                prompt_name=config.BRIEF_GENERATOR_PROMPT_NAME,
                prompt_version=config.BRIEF_GENERATOR_PROMPT_VERSION,
            )
            raise SystemExit(
                f"Refusing: this run would invalidate {dry_stats.invalidated_by_version_bump} previously-cached "
                f"briefs (${dry_stats.invalidated_cost_usd:.4f} to regenerate). "
                "Re-run with --acknowledge-cache-invalidation to proceed."
            )

        client = llm.LLMClient()

        stats = brief.run_brief_generation(
            conn, tickers=tickers, accession=args.accession, execute=True, client=client,
        )

        print(f"[EXECUTE] {stats.candidates} candidate filing(s), {stats.processed} processed.")
        print(
            f"  calls made: {stats.calls_made}, cache hits: {stats.cache_hits}, "
            f"no observations/findings: {stats.empty_no_input}, refused: {stats.refused}, "
            f"errors: {stats.errors}, truncated: {stats.truncated}, skipped (oversized): {stats.skipped_oversized}"
        )
        print(
            f"  briefs with sentences: {stats.briefs_with_sentences}, "
            f"empty after drop: {stats.briefs_empty_after_drop}, "
            f"generator dropped: {stats.generator_dropped_total}, verifier dropped: {stats.verifier_dropped_total}"
        )
        # L10: every paid command states this run's cost, lifetime spend,
        # and remaining budget on completion.
        print(
            f"  this run's cost: ${stats.run_cost_usd:.4f} | "
            f"lifetime spend: ${stats.total_cost_usd:.4f} of ${config.LLM_BUDGET_USD:.2f} budget | "
            f"remaining: ${config.LLM_BUDGET_USD - stats.total_cost_usd:.4f}"
        )

        if stats.stopped_reason is not None:
            print(f"  STOPPED EARLY: {stats.stopped_reason}")
            raise SystemExit(1)
    finally:
        conn.close()


def cmd_show_brief(args: argparse.Namespace) -> None:
    conn = db.get_connection()
    try:
        result = brief.get_latest_brief(conn, args.accession)
        if result is None:
            print(f"No brief found for accession {args.accession}.")
            return
        b, sentences = result["brief"], result["sentences"]
        print(
            f"Brief for {args.accession} "
            f"(prompt {b['prompt_version']}, verifier {b['verifier_version']}, model {b['model']})"
        )
        if not sentences:
            print("  (empty brief -- no material developments this period, or every sentence was dropped)")
            return
        for s in sentences:
            print(f"\n[{s['sentence_type']}] {s['text']}")
            for ref in json.loads(s["refs_json"]):
                resolved = brief.resolve_ref(conn, ref)
                if resolved is None:
                    print(f"    {ref}: (unresolved)")
                    continue
                row = resolved["row"]
                if resolved["kind"] == "observation":
                    print(f"    {ref}: [{row['severity']}] {row['statement']}")
                else:
                    print(f"    {ref}: [{row['severity']}] ({row['category']}) {row['headline']}")
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
        else:
            _print_row("TICKER", "FORM", "STATUS", "COUNT")
            for row in rows:
                _print_row(row["ticker"], row["form_type"], row["status"], str(row["n"]))

        fact_rows = conn.execute(
            """
            SELECT c.ticker, COUNT(*) AS n FROM xbrl_facts x
            JOIN companies c ON c.cik = x.cik
            GROUP BY c.ticker ORDER BY c.ticker
            """
        ).fetchall()
        print("\nXBRL facts:")
        if not fact_rows:
            print("  (none)")
        else:
            for row in fact_rows:
                print(f"  {row['ticker']}: {row['n']}")

        metric_rows = conn.execute(
            """
            SELECT c.ticker, COUNT(*) AS n FROM metrics m
            JOIN companies c ON c.cik = m.cik
            GROUP BY c.ticker ORDER BY c.ticker
            """
        ).fetchall()
        print("\nMetrics:")
        if not metric_rows:
            print("  (none)")
        else:
            for row in metric_rows:
                print(f"  {row['ticker']}: {row['n']}")

        observation_rows = conn.execute(
            """
            SELECT c.ticker, COUNT(*) AS n FROM observations o
            JOIN companies c ON c.cik = o.cik
            GROUP BY c.ticker ORDER BY c.ticker
            """
        ).fetchall()
        print("\nObservations:")
        if not observation_rows:
            print("  (none)")
        else:
            for row in observation_rows:
                print(f"  {row['ticker']}: {row['n']}")
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

    p_ingest = sub.add_parser("ingest-xbrl", help="Fetch companyfacts and store configured concepts")
    p_ingest.add_argument("--ticker", help="Restrict to one watchlist ticker")

    p_compute = sub.add_parser("compute-metrics", help="Compute the metric registry from xbrl_facts")
    p_compute.add_argument("--ticker", help="Restrict to one watchlist ticker")
    p_compute.add_argument("--metric", help="Recompute a single metric by name")

    p_backfill_readability = sub.add_parser(
        "backfill-readability", help="Populate normalized_text_hash and readability columns on sections"
    )
    p_backfill_readability.add_argument(
        "--force", action="store_true", help="Recompute rows already populated"
    )

    p_compute_obs = sub.add_parser(
        "compute-observations", help="Run the observation rule registry over metrics and sections"
    )
    p_compute_obs.add_argument("--ticker", help="Restrict to one watchlist ticker")
    p_compute_obs.add_argument("--rule", help="Recompute a single rule by name")

    p_validate = sub.add_parser("validate", help="Run data-quality checks against the real database")
    p_validate.add_argument("--ticker", help="Restrict to one watchlist ticker")

    p_analyze_sections = sub.add_parser(
        "analyze-sections", help="Run the section-analysis LLM prompt over eligible Notes sections"
    )
    p_analyze_sections.add_argument("--ticker", help="Restrict to one watchlist ticker")
    p_analyze_sections.add_argument("--accession", help="Restrict to a single filing by accession number")
    p_analyze_sections.add_argument(
        "--sample", type=int, default=None, help="Deterministically sample N sections (prompt development, never the full set)"
    )
    p_analyze_sections.add_argument(
        "--seed", type=int, default=None, help="Seed for --sample (default: config.DEFAULT_SAMPLE_SEED)"
    )
    p_analyze_sections.add_argument("--limit", type=int, default=None, help="Cap the number of sections processed")
    p_analyze_sections.add_argument(
        "--execute", action="store_true", help="Make real API calls. Without this flag, performs a dry run (no calls, no spend)."
    )
    p_analyze_sections.add_argument(
        "--confirm-cost", type=float, default=None,
        help=(
            "SPEC-006A L4: required when the dry-run estimate exceeds "
            f"${config.LLM_CONFIRM_THRESHOLD_USD:.2f} -- must match the printed estimate within a small tolerance."
        ),
    )
    p_analyze_sections.add_argument(
        "--acknowledge-cache-invalidation", action="store_true",
        help=(
            "SPEC-006A L5: required when a prompt-version bump would invalidate more than "
            f"{config.LLM_CACHE_INVALIDATION_WARN} previously-cached analyses."
        ),
    )
    p_analyze_sections.add_argument(
        "--max-run-cost", type=float, default=None,
        help=f"SPEC-006A L3: override the per-run cost ceiling (default ${config.LLM_MAX_RUN_COST_USD:.2f}).",
    )
    p_analyze_sections.add_argument(
        "--max-calls", type=int, default=None,
        help=f"SPEC-006A L6: override the per-run call-count ceiling (default {config.LLM_MAX_CALLS_PER_RUN}).",
    )
    p_analyze_sections.add_argument(
        "--scheduled", action="store_true",
        help=(
            "SPEC-006A L7: unattended-run mode. Clamps the run-cost ceiling to "
            f"${config.LLM_SCHEDULED_RUN_MAX_COST_USD:.2f} and refuses --sample."
        ),
    )

    sub.add_parser("spend", help="Report LLM spend: total, remaining, and breakdown by prompt/model/status")

    p_generate_briefs = sub.add_parser(
        "generate-briefs", help="Write a short grounded narrative brief per filing from observations and findings"
    )
    p_generate_briefs.add_argument("--ticker", help="Restrict to one watchlist ticker")
    p_generate_briefs.add_argument("--accession", help="Restrict to a single filing by accession number")
    p_generate_briefs.add_argument(
        "--execute", action="store_true", help="Make real API calls. Without this flag, performs a dry run (no calls, no spend)."
    )
    p_generate_briefs.add_argument(
        "--confirm-cost", type=float, default=None,
        help=(
            "SPEC-006A L4: required when the dry-run estimate exceeds "
            f"${config.LLM_CONFIRM_THRESHOLD_USD:.2f} -- must match the printed estimate within a small tolerance."
        ),
    )
    p_generate_briefs.add_argument(
        "--acknowledge-cache-invalidation", action="store_true",
        help=(
            "SPEC-006A L5: required when a prompt/verifier version bump would invalidate more than "
            f"{config.LLM_CACHE_INVALIDATION_WARN} previously-cached briefs."
        ),
    )

    p_show_brief = sub.add_parser("show-brief", help="Print a stored brief, each sentence with its type and resolved sources")
    p_show_brief.add_argument("--accession", required=True, help="Filing to show the brief for")

    sub.add_parser("status", help="Summarize filings by company, form, and status")

    return parser


def main(argv: list[str] | None = None) -> int:
    _setup_logging()
    # SPEC-006A L9: on startup, every invocation -- not gated to paid
    # commands. Warn, never refuse (the variable may legitimately be set for
    # an unrelated tool); the point is that it must never again go unnoticed.
    canary = llm.check_environment_canary()
    if canary is not None:
        print(f"\n{'!' * 70}\n{canary}\n{'!' * 70}\n", file=sys.stderr)

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
    elif args.command == "ingest-xbrl":
        cmd_ingest_xbrl(args)
    elif args.command == "compute-metrics":
        cmd_compute_metrics(args)
    elif args.command == "backfill-readability":
        cmd_backfill_readability(args)
    elif args.command == "compute-observations":
        cmd_compute_observations(args)
    elif args.command == "validate":
        cmd_validate(args)
    elif args.command == "analyze-sections":
        cmd_analyze_sections(args)
    elif args.command == "spend":
        cmd_spend(args)
    elif args.command == "generate-briefs":
        cmd_generate_briefs(args)
    elif args.command == "show-brief":
        cmd_show_brief(args)
    elif args.command == "status":
        cmd_status(args)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
