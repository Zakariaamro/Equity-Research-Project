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

import pandas as pd
import plotly.graph_objects as go
import streamlit as st

from dashboard import data, format as fmt
from edgar import config

# --- SPEC-008 review D1: escaping as structure, not memory ---
#
# The first D1 fix called `fmt.escape_markdown_currency` at each of seven
# call sites by hand -- reviewed and found wanting: nothing stops an eighth
# call site from being added without it (or one of the seven losing its call
# during a later edit) and both the unit tests and AppTest would still pass,
# since neither exercised the actual render path. app.py's own docstring
# makes the argument for structure over memory (explicit `url_path=` instead
# of a per-module naming convention); the same argument applies here.
#
# These three wrappers are now the ONLY way `brief_sentence`, `finding_item`,
# and `observation_item` reach Streamlit -- `test_narrative_renderers_use_the_
# escaping_wrapper` (test_dashboard_structure.py) greps their function bodies
# and fails the build if a raw `st.markdown`/`st.write`/`st.caption` call
# appears in any of them. A future call site can still forget to route
# through these three functions in the first place, but it can no longer
# forget the escape once it does -- and the grep test at least forces a
# conscious choice to bypass them, rather than a silent omission.
#
# Not made global (i.e. not applied to every st.* call in this module):
# most other call sites here (filing_header's dates/tickers, evidence_panel's
# concept names, metric_tile's st.metric) render fixed vocabulary or numbers
# with no '$' in them, or use widgets Streamlit does not markdown-parse in
# the first place (st.metric, st.code) -- escaping there would be a no-op at
# best. The three functions wrapped below are exactly the ones rendering
# model-/DB-generated prose, which is where an arbitrary '$' can appear.


def _safe_markdown(text: str) -> None:
    st.markdown(fmt.escape_markdown_currency(text))


def _safe_write(text: str) -> None:
    st.write(fmt.escape_markdown_currency(text))


def _safe_caption(text: str) -> None:
    st.caption(fmt.escape_markdown_currency(text))


# --- session_state keys (private to this module) ---

_AUTH_KEY = "_authenticated"
_SELECTED_TICKERS_KEY = "selected_tickers"
_CHART_CLICK_KEY_PREFIX = "chart_click__"
_SUB_TAB_KEY_PREFIX = "sub_tab__"


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


# --- environment visibility ---

# pyproject.toml's declared floor ("streamlit>=1.51") -- kept in sync by
# hand, same as `config.LLM_SONNET5_RATE_REVIEW_DATE`'s comment pointing
# back at its own source of truth. `st.column_config.TextColumn`'s
# `alignment` kwarg (added after 1.51.0, confirmed via `inspect.signature`,
# never trust the docs alone) was used in C4's rebuild and broke under a
# real 1.51.0 -- not this project's `.venv` (1.60.0) but a bare `streamlit`
# on PATH resolving to an unrelated Anaconda install instead. Removed, not
# floor-raised: a real reader's environment can be the declared minimum,
# not just whatever happens to be newest in whoever built this last.
_MIN_STREAMLIT_VERSION = (1, 51)


def environment_caption() -> None:
    """SPEC-008 review (found live, C4 rebuild): a Claude Code session's own
    `.venv` and an operator's actual `streamlit run` can silently resolve to
    two different Streamlit installs with two different feature surfaces --
    a gap that produces confusing, deep-in-a-page failures ('unexpected
    keyword argument') days after the code that used the missing feature
    was written and tested clean elsewhere. Always-visible, not a warning
    gated on a version floor: the point is to make 'which Streamlit is this,
    actually' answerable by looking at the sidebar, not by asking."""
    version = st.__version__
    parts = tuple(int(p) for p in version.split(".")[:2])
    caption = f"Streamlit {version}"
    if parts < _MIN_STREAMLIT_VERSION:
        st.sidebar.warning(f"{caption} -- below this project's declared floor ({'.'.join(map(str, _MIN_STREAMLIT_VERSION))}).")
    else:
        st.sidebar.caption(caption)


# --- C5: shared sub-tab bar ---


def sub_tab_bar(key: str, label: str, options: list[str]) -> str:
    """C5: a sub-tab bar rendered horizontally, directly above the content
    area -- the main sections stay in the left nav (`st.navigation`'s
    pages; this never adds another one). One component, used by any page
    that needs sub-tabs (Metrics by category is the first; built and
    proven against exactly that one consumer, not designed ahead against a
    second one that doesn't exist yet).

    `key` distinguishes multiple sub-tab bars living on different pages --
    this module still owns every `st.session_state` key in the app (hard
    rule 3); `st.radio`'s own `key=`-based widget state is the storage,
    same mechanism as `sidebar_company_selector`'s multiselect. Confirmed
    live: a keyed `st.radio`'s selection survives a full script rerun
    triggered by an unrelated widget changing (e.g. the sidebar company
    selector) exactly the way the company selector already survives page
    switching (R4a) -- the same guarantee, not a new one."""
    session_key = f"{_SUB_TAB_KEY_PREFIX}{key}"
    return st.radio(label, options=options, horizontal=True, key=session_key, label_visibility="collapsed")


# --- C4: the multi-period statement table ---

_LINE_ITEM_COL = "Line item"
_GROWTH_ROW_LABEL = " ↳ Growth %"  # em-space + turned arrow, indented under its value row

# SPEC-008 review (found live, C4): `st.columns`-per-row wraps a date header
# into a vertical stack of digits and splits a value across four lines the
# moment the table has more than a handful of periods -- `st.columns` was
# never a table widget, just N independently-wrapping blocks squeezed into a
# fixed fraction of page width. `st.dataframe` is an actual grid: it scrolls
# horizontally instead of wrapping, and `column_config`'s `pinned=True`
# freezes the label column natively -- no injected CSS, same discipline as
# the caret lesson.
#
# All periods are always shown -- no default trailing window, no "show full
# history" toggle (removed after live review: a hidden default was worse
# than a wide table). Landing on the newest period without scrolling was
# attempted three ways and all three are closed, checked against the
# installed 1.60.0 directly rather than assumed: (a) no scroll-to-position
# field exists on `st.dataframe`/`st.data_editor`'s Python signature OR the
# underlying `Dataframe` protobuf message (enumerated its fields directly --
# `arrow_data`, `id`, `columns`, `editing_mode`, `disabled`, `form_id`,
# `column_order`, `selection_mode`, `row_height`, `placeholder`,
# `selection_state`, `selection_default`, `button_click_widgets`; the
# selection-state schema itself is documented as selection-only, "Only
# selection events are supported at this time"); (b) a CSS `direction: rtl`
# trick would need an injected `<style>` block to survive, and it does not --
# the frontend bundle's own sanitizer call is literally `FORBID_TAGS:
# ['style']`, found in the shipped JS, not inferred; (c) `pinned` freezes a
# column to the LEFT edge only ("A pinned column will stay visible on the
# left side", every column type's own docstring) -- there is no right-pin,
# and pinning doesn't set the INITIAL scroll offset even where it exists, it
# only holds a column in place once already visible. Columns stay
# chronological (oldest left, newest right) pending a decision on whether to
# reverse them -- SPEC-008 review, C4 rebuild.
_BLANK_MARKERS = {"gap": "—", "split": "— °"}

_BLANK_FOOTNOTES = {
    "gap": "— not tagged/disclosed by this company for this line (see the Summary tab for one period's exact reason).",
    "split": "° this period's figure is in the line's OTHER row instead -- this line was split because its "
    "filed concept changed over time; a ° blank is a presentation choice, not a data gap.",
}

_ROW_HEIGHT_PX = 35
_HEADER_HEIGHT_PX = 38
_MAX_TABLE_HEIGHT_PX = 700

# SPEC-008-batch-1 render-batch follow-up item 1 (approved 2026-08-11): item
# 6's own note ("units and formatting are explicitly NOT implemented in this
# batch") blocked wiring EPS/shares into this table at all -- correct then,
# since `fmt.format_usd`'s $-millions convention would have shown a $2.35
# diluted EPS as "0". These two rows are the already-decided exception: the
# unit each canonical needs was specified in item 6's own spec text (EPS to
# two decimals in dollars, share counts in millions), not a new design
# choice made here. Every other canonical still falls through to
# `fmt.format_usd` below, unchanged.
_CELL_FORMATTERS = {
    "eps_basic": fmt.format_usd_per_share,
    "eps_diluted": fmt.format_usd_per_share,
    "basic_shares": fmt.format_shares,
    "diluted_shares": fmt.format_shares,
}


def _statement_table_style(row: pd.Series, row_kind: list[str], row_group: list[int]) -> list[str]:
    i = row.name
    style = "background-color: rgba(255, 255, 255, 0.04)" if row_group[i] % 2 else ""
    if row_kind[i] == "growth":
        # SPEC-008 C4 constraint 1, carried over from the st.columns design:
        # colour is the ONLY thing left distinguishing a number this project
        # DERIVED from one the company FILED, now that they're two rows
        # instead of a value-plus-caption. The row's own label (the turned
        # arrow + "Growth %", never repeated on a filed row) says the same
        # thing in words, redundantly -- so the distinction survives even if
        # this colour is ever lost to a theme change. Font weight, not
        # style: italic is not confirmed to survive Styler -> the dataframe
        # grid the way colour and weight are (Streamlit's own docs commit to
        # only "colors and font weights" for st.dataframe Styler support).
        style += "; color: #9aa0a6" if style else "color: #9aa0a6"
    return [style] * len(row)


def statement_table(rows: list[dict], periods: list[dict], show_growth: bool, key: str) -> None:
    """C4: line items as rows, periods as columns, oldest to newest left to
    right, ALL periods always shown -- `rows`/`periods` come straight from
    `data.get_income_statement_table`/`get_balance_sheet_table`/
    `get_cash_flow_table` (R8: growth% is already computed there; this
    function only renders what it's given). `key` namespaces this table's
    own grid instance so the Income statement/Balance sheet/Cash flow sub-
    tabs and every ticker's tab don't collide (`st.tabs`, like `st.columns`,
    still executes every tab's body on each rerun -- only the DISPLAY is
    gated, not the Python).

    Growth is now a SEPARATE ROW directly beneath its line item, not a
    caption beneath its cell (SPEC-008 review, C4 rebuild: `st.dataframe`
    has no per-cell second line the way `st.columns` + `st.caption` did) --
    see `_statement_table_style` for how constraint 1 (filed vs. derived
    must stay visually distinct, load-bearing not decorative) survives the
    move.

    A blank cell has two distinct causes (SPEC-008 C4 constraint 4) --
    never tagged/disclosed by this company, or the split-row complement
    (the other row carries this period instead). Marked distinctly per
    cell (`data._classify_blank_cell`) and explained in a footnote below,
    keyed to whichever causes actually appear. Never auto-hidden: a
    company's own absence of disclosure (e.g. AMZN's gross profit) is
    itself a fact this project exists to surface, not noise to collapse
    away.

    A NON-blank cell can still need a marker: cash-flow lines resolve to
    the true discrete quarter either because the company filed it directly
    (AMZN) or because it was DERIVED by subtracting the prior filed
    cumulative figure (MU, NVDA -- `edgar.metrics.
    compute_discrete_quarter_metrics`, SPEC-008 C4 approved 2026-08-08).
    `is_derived_quarter` marks the latter with a † suffix, same mechanism
    this table previously used for "filed at the wrong duration" -- that
    older meaning is retired along with the mechanism that produced it;
    † means only "derived by subtraction" now."""
    if not rows or not periods:
        empty_state("No periods with computed metrics for this company/basis yet.")
        return

    period_cols = [fmt.format_date(p["period_end"]) for p in periods]
    table_rows: list[dict] = []
    row_kind: list[str] = []
    row_group: list[int] = []
    any_derived_quarter = False
    blank_causes_present: set[str] = set()

    for group_idx, row in enumerate(rows):
        cells = row["cells"]

        value_entry = {_LINE_ITEM_COL: row["label"]}
        formatter = _CELL_FORMATTERS.get(row["canonical"], fmt.format_usd)
        for col_name, cell in zip(period_cols, cells):
            if cell["value"] is None:
                cause = cell.get("blank_cause", "gap")
                blank_causes_present.add(cause)
                value_entry[col_name] = _BLANK_MARKERS[cause]
                continue
            text = formatter(cell["value"])
            if cell.get("is_derived_quarter"):
                text += " †"
                any_derived_quarter = True
            value_entry[col_name] = text
        table_rows.append(value_entry)
        row_kind.append("value")
        row_group.append(group_idx)

        if show_growth:
            growth_entry = {_LINE_ITEM_COL: _GROWTH_ROW_LABEL}
            for col_name, cell in zip(period_cols, cells):
                # SPEC-008-batch-1 item 2 (D14): "n/m" whenever
                # data.py flagged the base as zero, sign-crossing, or
                # near-zero relative to this line's own typical size --
                # checked BEFORE the growth_pct is None case, since a
                # zero base has no growth_pct at all (division by zero)
                # but still needs "n/m", not a blank indistinguishable
                # from "no prior period to compare".
                if cell.get("growth_not_meaningful") is not None:
                    growth_entry[col_name] = "n/m"
                elif cell.get("growth_pct") is not None:
                    growth_entry[col_name] = fmt.format_growth_pct(cell["growth_pct"])
                else:
                    growth_entry[col_name] = ""
            table_rows.append(growth_entry)
            row_kind.append("growth")
            row_group.append(group_idx)

    df = pd.DataFrame(table_rows, columns=[_LINE_ITEM_COL] + period_cols)
    styled = df.style.apply(_statement_table_style, axis=1, row_kind=row_kind, row_group=row_group)

    column_config = {_LINE_ITEM_COL: st.column_config.TextColumn(_LINE_ITEM_COL, pinned=True)}
    for col_name in period_cols:
        column_config[col_name] = st.column_config.TextColumn(col_name, width="small")

    height = min(_HEADER_HEIGHT_PX + _ROW_HEIGHT_PX * len(table_rows), _MAX_TABLE_HEIGHT_PX)
    st.dataframe(
        styled, column_config=column_config, hide_index=True, height=height, key=f"table_grid__{key}",
    )

    if any_derived_quarter:
        st.caption(
            "† derived: this company files a cumulative (year-to-date) figure here, not a discrete quarterly "
            "one -- this cell subtracts the prior quarter's filed cumulative figure to isolate the quarter "
            "alone. Derived by this project, not filed; the filed cumulative figure is on the Summary tab."
        )
    for cause in ("split", "gap"):
        if cause in blank_causes_present:
            st.caption(_BLANK_FOOTNOTES[cause])
    if show_growth:
        st.caption(f"“{_GROWTH_ROW_LABEL.strip()}” rows are derived by this project, not filed.")


# --- empty / null states (SPEC-008 v1.1: legitimately empty says so, in words) ---


def empty_state(message: str) -> None:
    st.info(fmt.format_empty_section(message))


def null_metric_tile(label: str, null_reason: str | None) -> None:
    """SPEC-008 review D3 (found live): `st.metric`'s value string is
    truncated to the tile's column width -- putting the full reason there
    (as `format_null` alone would) satisfied AC9's letter while making it
    unreachable in practice, the exact defect this fixes. `st.metric`'s
    OWN value stays short ('Not available'); the full, untruncated reason
    goes in a caption line beneath it, where `st.caption` does not
    truncate. The caption shows the reason alone, not `format_null`'s
    "Not available — <reason>" -- the metric already says "Not available";
    repeating it in the caption right below was a real, reported
    redundancy, not a second, independent statement of the same fact."""
    st.metric(label, fmt.NOT_AVAILABLE)
    if null_reason:
        st.caption(null_reason)


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
    """SPEC-008 C2 (decided 2026-07-31, amends AC7 -- ARCHITECTURE.md
    decision log has the full sequence: v1's visible count line, v2's raw
    `<details>/<summary>` inline caret, and this v3).

    v2 was confirmed live to fail on two counts: the caret still rendered
    on its own line despite `display:inline` on every element (Streamlit
    sanitizes `unsafe_allow_html` content client-side via a bundled
    DOMPurify, which strips `style` attributes -- the CSS never reached the
    browser at all, not a CommonMark block-splitting issue as first
    diagnosed), and the injected `<style>` block coincided with the app's
    theme flipping from dark to light. v3 uses ONLY native Streamlit --
    `st.columns` with a narrow right-hand column holding `st.popover` for
    the sources, sentence text in the wide left column -- no raw HTML,
    nothing for a sanitizer to strip, nothing that can touch the app's
    theme. Confirmed live: the popover's own built-in disclosure indicator
    already renders a chevron -- giving it a "▾" label too doubled it up;
    the label is empty (`st.popover` requires a `str`, not `None`) and the
    widget's own indicator is the entire visible caret.

    The popover renders ONLY when the sentence has sources -- a sentence
    with none is a single, un-columned, full-width line with no caret at
    all, so its ABSENCE next to every grounded sentence around it is what
    makes it conspicuous (AC7's underlying point, same as v1/v2, carried by
    the caret's presence or absence rather than a visible count).

    The `[restatement]`/`[juxtaposition]`/`[grouping]` type prefix is
    dropped from DISPLAY only -- `sentence_type` stays in the database
    unchanged; SPEC-007 R4 still dispatches verification on it and
    ROADMAP-V2 still measures the type distribution from it. This was
    never a reader-facing distinction worth the space it took."""
    sources = sentence["sources"]
    if not sources:
        _safe_markdown(sentence["text"])
        return
    text_col, caret_col = st.columns([15, 1])
    with text_col:
        _safe_markdown(sentence["text"])
    with caret_col:
        with st.popover("", help=fmt.format_source_count(len(sources))):
            for source in sources:
                severity_label = fmt.format_severity_label(source["severity"])
                _safe_caption(f"— {severity_label}: {source['text']}")


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


def observation_ids_cited_in_brief(sentences: list[dict]) -> set[int]:
    """SPEC-008 review D10 (found live): the same observation routinely
    appears verbatim in both "The brief" (paraphrased by a restatement
    sentence) and "What changed?" (the observation itself) -- both sections
    draw from the same underlying corpus for the same filing, and a
    restatement is close to a direct rewording of its one cited
    observation. Used to exclude an observation already cited by a kept
    brief sentence from "What changed?", so the same fact is not listed
    twice on one page -- the richer, sourced version stays in the brief.

    Deliberately narrow: this is about not repeating an identical fact, not
    about how either section shows what it does show -- it changes neither
    section's own display rules (source visibility, click behaviour, ...).
    Includes every returned sentence, not just the top-N shown before the
    "Show all" expander -- everything `sentences` contains is genuinely on
    the page.

    Reads `sentence["sources"]` (`data.get_brief_sentences`'s already-
    resolved sources, each carrying `kind`/`row`), not the raw `refs`
    strings ("obs:1234") those sources were resolved from -- resolution,
    including telling an observation ref from a finding ref, already
    happened once in data.py; this does not redo it."""
    return {
        source["row"]["id"]
        for s in sentences
        for source in s["sources"]
        if source["kind"] == "observation"
    }


# --- observations, findings ---


def observation_item(obs: dict) -> None:
    severity_label = fmt.format_severity_label(obs["severity"])
    _safe_write(f"**{severity_label}** — {obs['statement']}")


def observations_section(observations: list[dict]) -> None:
    if not observations:
        empty_state("No observations for this filing.")
        return
    for obs in observations:
        observation_item(obs)


def finding_item(finding: dict, show_quote: bool = True) -> None:
    severity_label = fmt.format_severity_label(finding["severity"] or "low")
    _safe_write(f"**{severity_label}** ({finding['category']}) {finding['headline']}")
    if finding.get("detail"):
        _safe_caption(finding["detail"])
    if show_quote and finding.get("quote"):
        quote = finding["quote"]
        truncated = quote if len(quote) <= 400 else quote[:400] + "…"
        _safe_markdown(f"> {truncated}")
        if truncated != quote:
            with st.expander("Show full quote"):
                _safe_markdown(f"> {quote}")


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


def metric_tile(metric_def: "config.MetricDef", latest: dict | None, anchor_period_end: str | None = None) -> None:
    """One Overview/summary tile: value, formatted per metric_def, with its
    OWN period stated adjacent to it (v1.1) -- never inferred from the
    page's header alone, since an annual-basis metric's latest value can
    genuinely lag a quarterly anchor filing.

    `anchor_period_end` (SPEC-008 review D8, found live): Micron's cash row
    showed three tiles "as of May 28, 2026" and a fourth "FY as of Aug 28,
    2025" -- nine months older, labelled correctly but at the same type
    size and weight as its neighbours, easy to misread as comparable. When
    the tile's own period differs from the row's anchor filing period, the
    caption is bolded and says so explicitly -- weight, not colour, per the
    project's existing severity convention (R8) applied to the same
    principle here.

    D3 has TWO null paths, not one (found live, second review pass): the
    branch above (`latest is None`, no row computed at all) was fixed
    first, but Micron's actual tiles hit this one -- a row EXISTS, with
    `value is None` and a `null_reason`. `format_metric_value` folds that
    into one string, "Not available -- <reason>", which used to go straight
    into `st.metric` and get truncated exactly like the other path. Same
    fix, inline rather than via `null_metric_tile` (which has no period to
    show): short value in the metric, full reason in its own caption, and
    the row's real period still gets its own (D8-aware) caption -- a row
    that exists still has a genuine period, unlike the no-row-at-all case."""
    if latest is None:
        null_metric_tile(fmt.format_metric_label(metric_def), "no data computed for this company/period")
        return
    label = fmt.format_metric_label(metric_def)
    period_label = fmt.format_period_label(latest["period_end"], metric_def.basis)
    if anchor_period_end is not None and latest["period_end"] != anchor_period_end:
        period_caption = f"**{period_label} — a different period than this page's anchor filing**"
    else:
        period_caption = period_label
    if latest["value"] is None:
        st.metric(label, fmt.NOT_AVAILABLE)
        null_reason = latest.get("null_reason")
        if null_reason:
            # Reason alone, not `format_null`'s "Not available — <reason>"
            # -- the metric above already says "Not available".
            st.caption(null_reason)
        st.caption(period_caption)
        return
    value_str = fmt.format_metric_value(latest["value"], metric_def)
    st.metric(label, value_str)
    st.caption(period_caption)
    if metric_def.flag_threshold is not None:
        # SPEC-008 review D7 (found live): a bare "-2.75" means nothing to a
        # reader who does not already know beneish_m_score's own
        # conventional threshold. Not an interpretation of the number
        # (SPEC-008's own constraint) -- just the threshold it is measured
        # against, alongside it rather than only in a description field
        # nobody sees on this page.
        threshold_str = fmt.format_metric_value(metric_def.flag_threshold, metric_def)
        st.caption(f"Conventional flag threshold: {threshold_str}")


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
        else:
            # SPEC-008 review D9: an axis must read on the same scale as the
            # metric's own tile (percent as %, usd in millions, ...) -- the
            # same registry unit that fixes D4 drives this too.
            y_values = fmt.scale_for_axis(y_values, metric_def.unit)
        fig.add_trace(
            go.Scatter(
                x=[p["period_end"] for p in points], y=y_values, mode="lines+markers", name=ticker,
            )
        )
    ticksuffix = "" if scale == "indexed" else fmt.axis_ticksuffix(metric_def.unit)
    fig.update_layout(
        title=fmt.format_metric_label(metric_def),
        yaxis=dict(ticksuffix=ticksuffix),
        height=300,
        margin=dict(l=10, r=10, t=40, b=10),
    )
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
