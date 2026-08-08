"""SPEC-008 R1: all value formatting. The only place that decides how a
number looks -- driven entirely by `config.MetricDef.unit`/`precision`, never
by a page. No SQL, no Streamlit imports -- pure functions, easy to test
without a running app.
"""

from __future__ import annotations

from datetime import date, datetime

from edgar import config

NOT_AVAILABLE = "Not available"

_UNIT_SUFFIX = {
    "percent": "%",
    "usd": "",  # column header states "$m" explicitly (R1) -- no per-value suffix
    "usd_per_share": "",  # prefixed with "$" instead, see format_usd_per_share
    "days": " days",
    "ratio": "",
    "times": "x",
}


def _format_number(value: float, precision: int) -> str:
    """Thousands-separated, fixed precision -- shared by every unit."""
    return f"{value:,.{precision}f}"


def format_percent(value: float, precision: int) -> str:
    """A metric's raw value is a fraction (0.503, not 50.3) -- multiply by
    100 here, once, so no caller has to remember to."""
    return f"{_format_number(value * 100, precision)}%"


def format_usd(value: float, precision: int = 0) -> str:
    """R1: ALL usd values render in millions, everywhere, without exception
    -- Amazon's revenue shows as 637,960, not 638.0bn. No '$' prefix and no
    'm' suffix per value -- the column header states '$m' explicitly."""
    return _format_number(value / 1_000_000, precision)


def format_usd_per_share(value: float, precision: int = 2) -> str:
    """R1's stated exception: stays in dollars, not millions -- a $0.10 EPS
    impact is not 0.0000001 of anything useful.

    Deliberately NOT escaped here (SPEC-008 review, post-D1): this string's
    main destination is `st.metric` (via metric_tile), which does not
    markdown-parse its value at all -- pre-escaping would make it display
    the literal characters '\\$0.10' instead of '$0.10'. Escaping belongs at
    the point a formatted value reaches a markdown-parsed element
    (st.markdown/st.write/st.caption), not inside the formatter -- see
    escape_markdown_currency and its callers. The one other call site that
    reaches a markdown-parsed element with this value (pages/metrics.py's
    table view) escapes at that call site instead, once currently-safe (one
    value per st.write call, so no second '$' to pair with) but a landmine
    if that ever changes -- found in review, fixed rather than left."""
    return f"${_format_number(value, precision)}"


def format_days(value: float, precision: int = 0) -> str:
    return f"{_format_number(value, precision)} days"


def format_ratio(value: float, precision: int = 2) -> str:
    return _format_number(value, precision)


def format_times(value: float, precision: int = 2) -> str:
    return f"{_format_number(value, precision)}x"


_UNIT_FORMATTERS = {
    "percent": format_percent,
    "usd": format_usd,
    "usd_per_share": format_usd_per_share,
    "days": format_days,
    "ratio": format_ratio,
    "times": format_times,
}


def format_null(reason: str | None) -> str:
    """R5/R8 (v1.1): NULL is never rendered as zero or blank -- 'Not
    available', with the recorded reason whenever one exists. A missing
    input and a zero are different facts about the world."""
    if reason:
        return f"{NOT_AVAILABLE} — {reason}"
    return NOT_AVAILABLE


def format_metric_value(value: float | None, metric_def: "config.MetricDef", null_reason: str | None = None) -> str:
    """The single entry point pages/components should call for any metric
    value -- dispatches on `metric_def.unit`, handles NULL explicitly. Never
    silently renders a metric with an unrecognised unit; that is exactly the
    kind of failure `config._validate_metric_display_metadata` exists to
    catch at import time instead, but this still fails loudly here too,
    in case a future caller constructs a MetricDef by hand rather than
    reading it from the registry."""
    if value is None:
        return format_null(null_reason)
    formatter = _UNIT_FORMATTERS.get(metric_def.unit)
    if formatter is None:
        raise ValueError(f"Unrecognised unit {metric_def.unit!r} for metric {metric_def.name!r}")
    return formatter(value, metric_def.precision)


def format_empty_section(message: str) -> str:
    """SPEC-008 v1.1: any section that can legitimately be empty says so
    explicitly, in words -- never a blank space. A single, consistent entry
    point so every 'nothing here, and here's why' message reads the same
    way across the whole dashboard."""
    return message


def format_date(value: str | date | datetime | None) -> str:
    """'2026-02-06' -> 'Feb 6, 2026'. Accepts an ISO date string (as stored
    throughout the schema) or an already-parsed date/datetime."""
    if value is None:
        return NOT_AVAILABLE
    if isinstance(value, str):
        value = date.fromisoformat(value[:10])
    elif isinstance(value, datetime):
        value = value.date()
    return value.strftime("%b %-d, %Y") if hasattr(value, "strftime") else str(value)


def format_fiscal_period(fiscal_year: int | None, fiscal_period: str | None) -> str:
    """Same convention as analyze.py/brief.py's own copies (SPEC-006/007) --
    duplicated rather than imported, matching this project's established
    per-module-helper convention."""
    if fiscal_year is None or fiscal_period is None:
        return "unknown fiscal period"
    if fiscal_period == "FY":
        return f"FY{fiscal_year}"
    return f"{fiscal_period} FY{fiscal_year}"


def format_period_label(period_end: str, basis: str) -> str:
    """v1.1: every displayed value states its OWN period, adjacent to it --
    never inferred from the page's header alone. `basis` distinguishes an
    annual figure ('FY, as of <date>') from a quarterly one ('as of <date>'),
    since an annual-basis metric's 'latest' value can genuinely be a full
    fiscal year older than a quarterly anchor filing."""
    label = format_date(period_end)
    return f"FY as of {label}" if basis == "annual" else f"as of {label}"


_DURATION_LABELS = {
    "quarterly": "three-month",
    "half-year": "six-month",
    "three-quarter": "nine-month",
    "annual": "FY",
}


def format_duration_label(period_class: str) -> str:
    """SPEC-008 review D11 (found live): a period-END date alone does not
    say how long the period covering it was -- Micron's Financials page
    showed "Revenue: 78,959" labelled only "as of May 28, 2026", which was
    actually the nine-month year-to-date cumulative, not the three-month
    quarter the Overview page's own margins are computed from. Maps
    `data.get_period_duration_class`'s classification (shared with
    `_classify_duration`'s "quarterly"/"half-year"/"three-quarter"/"annual")
    to the human phrase a reader recognises. Empty string for "instant"
    (a balance-sheet figure is as-of a date, not FOR a period -- nothing to
    state) and "other" (no real duration fact for any of the three
    companies has ever fallen outside the named bands; an empty label here
    is deliberately non-committal rather than a guess)."""
    return _DURATION_LABELS.get(period_class, "")


def format_source_count(count: int) -> str:
    """SPEC-008 C2 (decided 2026-07-31, amends AC7 -- see ARCHITECTURE.md's
    decision log): sources move behind a click, but this count does not --
    it is what a reader sees without clicking anything, on every sentence,
    including the zero case. Singular/plural, not "1 sources"."""
    return "1 source" if count == 1 else f"{count} sources"


_LABEL_UNIT_SUFFIX = {
    "usd": " ($m)",  # every other unit self-labels its value (%, x, ' days'); usd's own value never does (R1)
}


def format_metric_label(metric_def: "config.MetricDef") -> str:
    """SPEC-008 review D4 (found live): R1 says a usd VALUE carries no
    per-value suffix because 'the column header states $m explicitly' --
    true on the Financials page's statement tables (which do print that
    header), but `metric_tile`/`metric_chart` have no table header for it to
    land on, so usd tiles/charts rendered a bare, unlabeled number
    (Amazon's free cash flow: '-18,171', could be thousands, millions, or
    billions). Every other unit is self-evident from its own value's
    suffix; usd is the one exception, so it is the only one needing this."""
    return metric_def.display_name + _LABEL_UNIT_SUFFIX.get(metric_def.unit, "")


_AXIS_SCALE_FACTOR = {
    "percent": 100,  # matches format_percent's *100
    "usd": 1 / 1_000_000,  # matches format_usd's /1_000_000
}


def scale_for_axis(values: list[float], unit: str) -> list[float]:
    """SPEC-008 review D9: chart axes plotted the raw metric value
    regardless of unit, so a percent metric's axis read '0.4, 0.45, 0.5'
    while its own tile, one section up, read '51.82%' -- same quantity, two
    different scales. Applies the same registry-driven scale format.py's
    single-value formatters already use, so an axis and a tile for the same
    metric always agree."""
    factor = _AXIS_SCALE_FACTOR.get(unit, 1)
    return [v * factor for v in values]


def axis_ticksuffix(unit: str) -> str:
    """The same per-unit suffix `_UNIT_FORMATTERS` puts on a single
    formatted value, reused for a chart's y-axis ticks (SPEC-008 review D9)."""
    return _UNIT_SUFFIX.get(unit, "")


def format_severity_label(severity: str) -> str:
    """R8: severity is conveyed by an explicit text label and by ordering,
    never by colour. This is the one place that decides the label text, so
    every page reads the same word for the same severity."""
    return {"high": "High", "medium": "Medium", "low": "Low"}.get(severity, severity.title())


def escape_markdown_currency(text: str) -> str:
    """SPEC-008 review D1 (found live, real browser): Streamlit's markdown
    renderer treats `$...$` as inline LaTeX math mode. Any narrative
    sentence with two dollar figures -- e.g. "a $35.0 billion commitment
    and a $20.0 billion facility" -- gets everything between the two `$`
    swallowed into math mode: spaces vanish, the font goes italic serif.
    AppTest can't catch this, since it asserts on the string handed to
    Streamlit, not on how a real browser renders it -- this only surfaced
    once someone actually looked at the page.

    Escaping every literal '$' as '\\$' turns it back into a plain
    character. Apply this at the point any model-/DB-generated prose
    reaches `st.markdown`/`st.write`/`st.caption` -- never by editing the
    stored text itself, which is correct; only the rendering was wrong."""
    return text.replace("$", r"\$")


def format_growth_pct(value: float | None, precision: int = 1) -> str:
    """SPEC-008 C4: period-over-period % change, computed in the data layer
    (R8) and formatted here for display beneath a statement cell. Always
    signed (a bare "10.0%" makes a reader infer direction; "+10.0%" /
    "-10.0%" never requires that). Empty string, not "N/A" or "0.0%", when
    there is nothing to show -- the caller (`components`) decides whether
    to render anything at all; this never fabricates a rate where none was
    computed."""
    if value is None:
        return ""
    sign = "+" if value >= 0 else ""
    return f"{sign}{value * 100:.{precision}f}%"
