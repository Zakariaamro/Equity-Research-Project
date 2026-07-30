"""SPEC-008 R4: Financials page. Composition only."""

from __future__ import annotations

import streamlit as st

from dashboard import components, data, format as fmt
from edgar import config, section_store


def _render_summary(cik: str, period_end: str) -> None:
    for title, lines in (
        ("Income statement", data.INCOME_STATEMENT_LINES),
        ("Balance sheet", data.BALANCE_SHEET_LINES),
        ("Cash flow", data.CASH_FLOW_LINES),
    ):
        st.markdown(f"**{title}** ($m)")
        values = data.get_statement_line_values(cik, period_end, lines)
        for row in values:
            if row["value"] is None:
                st.write(f"{row['label']}: {fmt.format_null('not tagged for this period')}")
            else:
                st.write(f"{row['label']}: {fmt.format_usd(row['value'])}")


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
    labels = [fmt.format_date(p["period_end"]) for p in period_rows]
    choice = st.selectbox(f"Period ({ticker})", options=range(len(period_rows)), format_func=lambda i: labels[i], key=f"period_{ticker}")
    period_end = period_rows[choice]["period_end"]

    summary_tab, as_filed_tab = st.tabs(["Summary", "As filed"])
    with summary_tab:
        st.caption(f"Curated lines, built from xbrl_facts, as of {fmt.format_date(period_end)}. Not full GAAP presentation.")
        _render_summary(cik, period_end)
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
