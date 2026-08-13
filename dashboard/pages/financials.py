"""SPEC-008 R4: Financials page. Composition only."""

from __future__ import annotations

import streamlit as st

from dashboard import components, data, format as fmt
from edgar import config, section_store


def _render_summary(cik: str, ticker: str, period_start: str, period_end: str) -> None:
    # SPEC-008 review D11 (found live): Micron's Financials page showed
    # "Revenue: 78,959" labelled only "as of May 28, 2026" -- actually the
    # nine-month year-to-date cumulative, not the three-month quarter the
    # Overview page's own margins are computed from, with nothing on
    # either page saying the two use different durations. Every
    # duration-based section now states its own duration explicitly.
    # Balance-sheet lines are as-of a single date, not for a period, so
    # they get no duration phrase.
    duration_label = fmt.format_duration_label(data.get_period_duration_class(period_start, period_end))

    # D11 follow-up (found live, confirmed against the real corpus): a
    # 10-Q's cash-flow statement is routinely tagged year-to-date only --
    # confirmed for Micron and NVIDIA, where most non-Q1 quarters have no
    # three-month cash-flow facts at all. Forcing the selector's quarterly
    # period_start there turns a real fact at a different duration into
    # "not tagged," which reads as a data gap rather than what it is.
    cash_flow_period_start, cash_flow_note = data.get_cash_flow_period(cik, period_start, period_end)
    cash_flow_duration_label = fmt.format_duration_label(
        data.get_period_duration_class(cash_flow_period_start, period_end)
    )

    sections = (
        ("Income statement", data.INCOME_STATEMENT_LINES, period_start, duration_label, ""),
        ("Balance sheet", data.BALANCE_SHEET_LINES, period_start, "", ""),
        ("Cash flow", data.CASH_FLOW_LINES, cash_flow_period_start, cash_flow_duration_label, cash_flow_note),
    )
    for title, lines, line_period_start, line_duration_label, note in sections:
        header = f"**{title}** ($m)"
        if line_duration_label:
            header += f", {line_duration_label} period"
        if note:
            header += f" ({note})"
        st.markdown(header)
        values = data.get_statement_line_values(cik, line_period_start, period_end, lines)
        for row in values:
            if row["value"] is None:
                if note and row["canonical"] in config.METRIC_REGISTRY:
                    # Found live: MU's free cash flow read "not tagged for
                    # this period" immediately beneath its own two inputs
                    # (cash from operations, capital expenditure), both
                    # visibly present -- looked like a bug, not a boundary.
                    # It IS present, at the metric's own computed duration
                    # (three-month); this section is showing cfo/capex at
                    # the year-to-date fallback duration instead (the note
                    # above), and free_cash_flow was never computed at
                    # that duration. This page does not derive on the fly
                    # (`get_statement_line_values` already reads
                    # free_cash_flow from `metrics`, not from cfo - capex
                    # computed here) -- the reason says why THIS number is
                    # unavailable at the duration its neighbours happen to
                    # be shown at, not a blanket claim this page never
                    # shows derived figures (it does; this is one).
                    reason = (
                        f"not computed at this duration -- {row['label']} is stored only for the "
                        "three-month figures; this section is showing the year-to-date figures "
                        "instead, since the three-month facts aren't tagged separately this quarter"
                    )
                else:
                    # SPEC-008 review, debt-line follow-up (found live): "not
                    # tagged for this period" wrongly implies a period-specific
                    # gap for a concept a company has never tagged at all
                    # (Micron's debt_noncurrent, 100% of periods) -- the reason
                    # says which is actually true. Decides nothing about which
                    # concept would be a safe fallback to show instead.
                    reason = data.get_statement_line_null_reason(cik, row["canonical"], ticker)
                st.write(f"{row['label']}: {fmt.format_null(reason)}")
            else:
                st.write(f"{row['label']}: {fmt.format_usd(row['value'])}")


_STATEMENT_TABLE_FUNCS = {
    "Income statement": data.get_income_statement_table,
    "Key metrics": data.get_key_metrics_table,
    "Balance sheet": data.get_balance_sheet_table,
    "Cash flow": data.get_cash_flow_table,
}
# SPEC-008-batch-1 render-batch follow-up item 1 (approved 2026-08-11): item
# 6 built the underlying table function but never added it to the tab dict
# above -- the rows existed in the data layer and simply had no display path
# at all. A separate sub-tab, not merged into "Income statement"'s own rows:
# EPS is dollars-per-share, share counts are a raw count, free cash flow/
# FCFF/FCFE are $-millions same as every other statement, and the effective
# tax rate is a percent -- no single unit caption covers all of it, so this
# tab states its own units per group rather than claiming "$m" the way
# every other statement's caption does.
# SPEC-008-batch-2 item 2 (approved 2026-08-13): renamed "EPS & shares" ->
# "Key metrics" and widened with free_cash_flow/fcff/fcfe/fcff_tax_rate --
# see KEY_METRICS_LINES in dashboard/data.py for the full reasoning,
# especially FCFF's tax-rate caveat.
_KEY_METRICS_CAPTION = "EPS in $ (2dp), shares and cash-flow figures in millions ($m), tax rate in %"


def _render_table(cik: str, ticker: str) -> None:
    """C4: line items as rows, periods as columns, oldest to newest left to
    right. Sub-tabs (C5's shared component) for Income statement/Balance
    sheet/Cash flow; annual/quarterly toggle; a growth-% toggle whose
    values are computed entirely in `data.py` (R8), never here."""
    basis = st.radio(
        f"Period ({ticker})", options=["annual", "quarterly"], horizontal=True, key=f"table_basis_{ticker}",
    )
    show_growth = st.toggle(
        "Show period-over-period growth %", key=f"table_growth_{ticker}",
        help="Derived by this project, not filed -- shown beneath each figure, in italic and dim.",
    )
    statement = components.sub_tab_bar(
        f"table_statement_{ticker}", "Statement", list(_STATEMENT_TABLE_FUNCS.keys())
    )

    periods = data.get_statement_periods(cik, basis)
    if not periods:
        components.empty_state(f"No {basis} periods computed yet for {ticker}.")
        return

    # D11: every column states its duration -- uniform across the whole
    # table (the nominal basis the reader chose), stated once rather than
    # repeated per column. Cash flow's per-cell exceptions (the D11
    # follow-up fallback) get their own marker inside the table itself.
    duration_label = fmt.format_duration_label(
        data.get_period_duration_class(periods[0]["period_start"], periods[0]["period_end"])
    )
    # Key metrics is the one statement not uniformly $m -- see _KEY_METRICS_CAPTION.
    unit_caption = _KEY_METRICS_CAPTION if statement == "Key metrics" else "$m"
    st.caption(f"{unit_caption}, {duration_label} periods" if duration_label else unit_caption)

    rows = _STATEMENT_TABLE_FUNCS[statement](cik, periods, ticker)
    components.statement_table(rows, periods, show_growth, key=f"{ticker}_{statement}")


def _render_as_filed(cik: str) -> None:
    sections = data.get_as_filed_sections(cik)
    if not sections:
        components.empty_state("No 'as filed' statement sections available for this company.")
        return
    labels = [f"{s['form_type']} {fmt.format_date(s['filing_date'])} — {s['short_name']}" for s in sections]
    choice = st.selectbox("Section", options=range(len(sections)), format_func=lambda i: labels[i])
    section = sections[choice]
    text = section_store.read_section_text(section["text_hash"])
    st.text(text)


def _render_company(ticker: str, cik: str) -> None:
    period_rows = data.get_statement_period_ends(cik)
    if not period_rows:
        components.empty_state(f"No metrics computed yet for {ticker}.")
        return

    def _label(p: dict) -> str:
        # SPEC-008 review D11: a period-end date alone doesn't say whether
        # this option is the three-month quarter or a nine-month
        # year-to-date cumulative ending the same day -- state it here, at
        # the point of choosing, not only after the fact in the sections
        # below.
        duration = fmt.format_duration_label(data.get_period_duration_class(p["period_start"], p["period_end"]))
        date_label = fmt.format_date(p["period_end"])
        return f"{date_label} ({duration} period)" if duration else date_label

    labels = [_label(p) for p in period_rows]
    choice = st.selectbox(f"Period ({ticker})", options=range(len(period_rows)), format_func=lambda i: labels[i], key=f"period_{ticker}")
    period_start = period_rows[choice]["period_start"]
    period_end = period_rows[choice]["period_end"]

    summary_tab, table_tab, as_filed_tab = st.tabs(["Summary", "Table", "As filed"])
    with summary_tab:
        st.caption(f"Curated lines, built from xbrl_facts, as of {fmt.format_date(period_end)}. Not full GAAP presentation.")
        _render_summary(cik, ticker, period_start, period_end)
    with table_tab:
        _render_table(cik, ticker)
    with as_filed_tab:
        st.caption("Statements exactly as SEC rendered them.")
        _render_as_filed(cik)


def render() -> None:
    st.title("Financials")
    selected_tickers = components.get_selected_tickers()
    companies = {c["ticker"]: c["cik"] for c in data.get_companies()}
    tabs = st.tabs(selected_tickers)
    for tab, ticker in zip(tabs, selected_tickers):
        with tab:
            _render_company(ticker, companies[ticker])
