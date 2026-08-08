"""SPEC-008 R3: Overview page. Composition only -- no SQL, no formatting, no
display logic, no direct session-state access. Fetch, arrange, done."""

from __future__ import annotations

import streamlit as st

from dashboard import components, data
from edgar import config

_SPARKLINE_METRICS = ("revenue_yoy", "gross_margin", "operating_margin", "roic")
_CASH_METRICS = ("capex_to_revenue", "capex_to_depreciation", "free_cash_flow", "cash_conversion_cycle")
_SUSPICION_METRICS = ("beneish_m_score", "depreciation_rate", "inventory_growth_less_revenue_growth")


def _render_company(ticker: str, cik: str) -> None:
    anchor = data.get_anchor_filing(cik)
    if anchor is None:
        components.empty_state(f"No 10-K or 10-Q on file yet for {ticker}.")
        return
    more_recent_8k = data.get_more_recent_8k(cik, anchor["filing_date"])
    components.filing_header(anchor, more_recent_8k)

    st.markdown("### The brief")
    sentences = data.get_brief_sentences(anchor["accession_no"])
    components.brief_section(sentences, top_n=6)

    st.markdown("### What changed?")
    observations = data.get_top_observations(anchor["accession_no"])
    already_in_brief = components.observation_ids_cited_in_brief(sentences)
    observations = [o for o in observations if o["id"] not in already_in_brief]
    components.observations_section(observations)

    st.markdown("### Is the business getting better or worse?")
    cols = st.columns(len(_SPARKLINE_METRICS))
    for col, name in zip(cols, _SPARKLINE_METRICS):
        metric_def = config.METRIC_REGISTRY[name]
        with col:
            series = data.get_metric_series(cik, name, metric_def.basis)
            components.metric_chart(
                metric_def, {ticker: series}, {ticker: cik}, key_prefix=f"overview_{ticker}_{name}"
            )

    st.markdown("### Where is the cash going?")
    cols = st.columns(len(_CASH_METRICS))
    for col, name in zip(cols, _CASH_METRICS):
        metric_def = config.METRIC_REGISTRY[name]
        with col:
            latest = data.get_latest_metric(cik, name, metric_def.basis)
            components.metric_tile(metric_def, latest, anchor_period_end=anchor["period_end"])

    st.markdown("### Should I be suspicious?")
    cols = st.columns(len(_SUSPICION_METRICS))
    for col, name in zip(cols, _SUSPICION_METRICS):
        metric_def = config.METRIC_REGISTRY[name]
        with col:
            latest = data.get_latest_metric(cik, name, metric_def.basis)
            components.metric_tile(metric_def, latest, anchor_period_end=anchor["period_end"])
    red_flags = data.get_red_flag_findings(anchor["accession_no"])
    components.red_flag_section(red_flags)

    st.caption(
        "A fifth question -- *can I trust management?* -- needs guidance tracking, which "
        "does not exist yet. Planned, not shown."
    )


def render() -> None:
    st.title("Overview")
    selected_tickers = components.get_selected_tickers()
    companies = {c["ticker"]: c["cik"] for c in data.get_companies()}
    tabs = st.tabs(selected_tickers)
    for tab, ticker in zip(tabs, selected_tickers):
        with tab:
            _render_company(ticker, companies[ticker])
