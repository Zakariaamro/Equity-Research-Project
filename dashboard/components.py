"""SPEC-008: ALL display logic. `metric_card`, `metric_chart`,
`evidence_panel`, ... A page composes these; it does not render anything
itself.

**This module owns every `st.session_state` key in the whole app** (SPEC-008
v1.1) -- the sidebar company selection, the auth gate's "already
authenticated" flag, and each chart's click-state for its evidence panel.
Pages, and `app.py`, read and write this state only through the named
functions below (`get_selected_tickers`, `require_auth`, ...), never through
`st.session_state` directly -- enforced by
`test_no_session_state_outside_components` (grep-style, mirroring the
existing no-SQL-outside-data-module check).
"""

from __future__ import annotations

import plotly.graph_objects as go
import streamlit as st

from dashboard import data, format as fmt
from edgar import config

# --- session_state keys (private to this module) ---

_AUTH_KEY = "_authenticated"
_SELECTED_TICKERS_KEY = "selected_tickers"
_CHART_CLICK_KEY_PREFIX = "chart_click__"


# --- R2: auth gate ---


def _expected_password() -> str | None:
    """st.secrets raises StreamlitSecretNotFoundError (not just returning
    None/{}) when no secrets.toml exists at all -- a real thing found while
    first running this locally, not a hypothetical. Treated as 'no password
    configured', same as the key being absent from a real secrets file."""
    try:
        return st.secrets.get("dashboard_password")
    except Exception:
        return None


def require_auth() -> bool:
    """Simple password gate via st.secrets, checked before any page renders
    (R2) -- the L8 guardrail from SPEC-006A arriving early. Returns True once
    the correct password has been entered this session; renders the gate
    itself and returns False otherwise, so the caller (app.py) can stop."""
    if st.session_state.get(_AUTH_KEY):
        return True
    st.title("Equity Research Dashboard")
    password = st.text_input("Password", type="password")
    expected = _expected_password()
    if expected is None:
        st.warning("No dashboard_password configured in .streamlit/secrets.toml -- refusing to serve any page.")
        return False
    if password and password == expected:
        st.session_state[_AUTH_KEY] = True
        st.rerun()
    elif password:
        st.error("Incorrect password.")
    return False


# --- R4a: the one company selector ---


def sidebar_company_selector() -> list[str]:
    """R4a: a multi-select in the sidebar, defaulting to all three
    companies, persisting across pages. Called exactly once, from `app.py`,
    before dispatching to whichever page was selected (v1.1: built on
    `st.navigation`/`st.Page`, which makes this possible -- see
    ARCHITECTURE.md and SPEC-008 v1.1's rationale)."""
    companies = data.get_companies()
    options = [c["ticker"] for c in companies]
    default = st.session_state.get(_SELECTED_TICKERS_KEY, options)
    selected = st.sidebar.multiselect("Companies", options=options, default=default, key=_SELECTED_TICKERS_KEY)
    return selected or options


def get_selected_tickers() -> list[str]:
    """How a page reads the current sidebar selection, without touching
    `st.session_state` itself."""
    companies = data.get_companies()
    return st.session_state.get(_SELECTED_TICKERS_KEY) or [c["ticker"] for c in companies]


# --- empty / null states (SPEC-008 v1.1: legitimately empty says so, in words) ---


def empty_state(message: str) -> None:
    st.info(fmt.format_empty_section(message))


def null_metric_tile(label: str, null_reason: str | None) -> None:
    st.metric(label, fmt.format_null(null_reason))


# --- header ---


def filing_header(filing: dict, more_recent_8k: dict | None) -> None:
    """SPEC-008 R3 / v1.1: anchors to the latest 10-K/10-Q, never an 8-K. A
    more recent or same-day 8-K is noted, with a link to the Filings page --
    never used as the anchor and never silently dropped."""
    ticker = filing.get("ticker", "")
    company = filing.get("company_name", "")
    st.subheader(f"{company} ({ticker})")
    sec_link = (
        f"https://www.sec.gov/cgi-bin/browse-edgar?action=getcompany&CIK={filing['cik']}"
    )
    st.write(
        f"{filing['form_type']} filed {fmt.format_date(filing['filing_date'])} "
        f"(period {fmt.format_date(filing['period_end'])}) — [View on SEC]({sec_link})"
    )
    if more_recent_8k is not None:
        st.caption(
            f"A more recent 8-K was filed {fmt.format_date(more_recent_8k['filing_date'])}. "
            f"See the Filings page for {more_recent_8k['accession_no']}."
        )


# --- the brief ---


def brief_sentence(sentence: dict) -> None:
    """SPEC-007 Residual Risk / SPEC-008 hard rule 3: sources render
    immediately beneath the sentence, never behind a click."""
    st.markdown(f"**[{sentence['sentence_type']}]** {sentence['text']}")
    for source in sentence["sources"]:
        severity_label = fmt.format_severity_label(source["severity"])
        st.caption(f"— {severity_label}: {source['text']}")


def brief_section(sentences: list[dict], top_n: int = 6) -> None:
    """R3: top N sentences by the maximum severity of their cited sources;
    'Show all N sentences' expands the rest."""
    if not sentences:
        empty_state("No brief exists for this filing.")
        return
    ranked = sorted(sentences, key=lambda s: s["max_source_severity"])
    top = ranked[:top_n]
    rest = ranked[top_n:]
    for s in top:
        brief_sentence(s)
    if rest:
        with st.expander(f"Show all {len(sentences)} sentences"):
            for s in rest:
                brief_sentence(s)


# --- observations, findings ---


def observation_item(obs: dict) -> None:
    severity_label = fmt.format_severity_label(obs["severity"])
    st.write(f"**{severity_label}** — {obs['statement']}")


def observations_section(observations: list[dict]) -> None:
    if not observations:
        empty_state("No observations for this filing.")
        return
    for obs in observations:
        observation_item(obs)


def finding_item(finding: dict, show_quote: bool = True) -> None:
    severity_label = fmt.format_severity_label(finding["severity"] or "low")
    st.write(f"**{severity_label}** ({finding['category']}) {finding['headline']}")
    if finding.get("detail"):
        st.caption(finding["detail"])
    if show_quote and finding.get("quote"):
        quote = finding["quote"]
        truncated = quote if len(quote) <= 400 else quote[:400] + "…"
        st.markdown(f"> {truncated}")
        if truncated != quote:
            with st.expander("Show full quote"):
                st.markdown(f"> {quote}")


def red_flag_section(red_flags: list[dict]) -> None:
    """SPEC-008 v1.1: 'No red-flag findings in this filing' is information;
    a blank space reads as a bug. This is the common case, not the rare one
    -- confirmed live, zero of the corpus's two red_flag findings fall on
    any of the three companies' current latest filing."""
    if not red_flags:
        empty_state("No red-flag findings in this filing.")
        return
    for f in red_flags:
        finding_item(f)


# --- metrics ---


def metric_tile(metric_def: "config.MetricDef", latest: dict | None) -> None:
    """One Overview/summary tile: value, formatted per metric_def, with its
    OWN period stated adjacent to it (v1.1) -- never inferred from the
    page's header alone, since an annual-basis metric's latest value can
    genuinely lag a quarterly anchor filing."""
    if latest is None:
        null_metric_tile(metric_def.display_name, "no data computed for this company/period")
        return
    value_str = fmt.format_metric_value(latest["value"], metric_def, latest.get("null_reason"))
    st.metric(metric_def.display_name, value_str)
    st.caption(fmt.format_period_label(latest["period_end"], metric_def.basis))


def metric_chart(
    metric_def: "config.MetricDef",
    series_by_ticker: dict[str, list[dict]],
    cik_by_ticker: dict[str, str],
    key_prefix: str,
    scale: str = "absolute",
) -> None:
    """R5: one chart per metric, over time, for every company in
    `series_by_ticker`. Click a point -> evidence panel. This function owns
    its own click-state key (v1.1) -- pages never touch st.session_state."""
    fig = go.Figure()
    for ticker, series in series_by_ticker.items():
        points = [p for p in series if p["value"] is not None]
        if not points:
            continue
        y_values = [p["value"] for p in points]
        if scale == "indexed" and metric_def.unit == "usd" and y_values[0] not in (0, None):
            base = y_values[0]
            y_values = [v / base * 100 for v in y_values]
        fig.add_trace(
            go.Scatter(
                x=[p["period_end"] for p in points], y=y_values, mode="lines+markers", name=ticker,
            )
        )
    fig.update_layout(title=metric_def.display_name, height=300, margin=dict(l=10, r=10, t=40, b=10))
    click_key = f"{_CHART_CLICK_KEY_PREFIX}{key_prefix}"
    event = st.plotly_chart(fig, key=f"{key_prefix}_chart", on_select="rerun", selection_mode="points")
    if event and event.get("selection", {}).get("points"):
        st.session_state[click_key] = event["selection"]["points"][0]
    clicked = st.session_state.get(click_key)
    if clicked is not None:
        ticker = clicked.get("curve_number")
        # Map curve index back to ticker (traces added in dict-iteration order).
        tickers = list(series_by_ticker.keys())
        if isinstance(ticker, int) and 0 <= ticker < len(tickers):
            resolved_ticker = tickers[ticker]
            period_end = clicked.get("x")
            cik = cik_by_ticker.get(resolved_ticker)
            if cik and period_end:
                matching = next((p for p in series_by_ticker[resolved_ticker] if p["period_end"] == period_end), None)
                period_start = matching["period_start"] if matching else None
                evidence = data.get_metric_evidence(cik, metric_def.name, period_start, period_end)
                if evidence is not None:
                    evidence_panel(evidence, metric_def, resolved_ticker, period_end)


def evidence_panel(evidence: dict, metric_def: "config.MetricDef", ticker: str, period_end: str) -> None:
    """R5: formula, input values, source filing, link to SEC."""
    with st.container(border=True):
        st.write(f"**Evidence** — {metric_def.display_name}, {ticker}, {fmt.format_date(period_end)}")
        st.code(evidence["formula"], language=None)
        st.write("Inputs:")
        for concept, value in evidence["inputs_used"].items():
            st.write(f"- {concept}: {value:,.0f}")
        accession = evidence["accession_no"]
        if accession:
            st.write(f"Source filing: `{accession}` — [View on SEC](https://www.sec.gov/cgi-bin/browse-edgar?action=getcompany)")
