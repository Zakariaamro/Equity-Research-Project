"""SPEC-008 R1: all value formatting. The only place that decides how a
number looks -- driven entirely by `config.MetricDef.unit`/`precision`, never
by a page. No SQL, no Streamlit imports -- pure functions, easy to test
without a running app.
"""

from __future__ import annotations

import re
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


def format_shares(value: float, precision: int = 0) -> str:
    """SPEC-008-batch-1 render-batch follow-up item 1 (approved 2026-08-11):
    a share count, in millions -- same division as format_usd (millions is
    the right scale for these companies' share counts too) but kept as its
    own function since it is not a dollar amount; nothing here implies '$'
    the way format_usd's column header does."""
    return _format_number(value / 1_000_000, precision)


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


# Reused, not reinvented: `section_store.normalize_for_wording_hash` (SPEC-005)
# already strips this exact artifact for its own purpose (wording-identity
# hashing) using this pattern from config -- it handles a variable number of
# dot-separated version segments ("v3.25.0.1" as well as "v3.26.1", noted in
# that function's own docstring), which a hand-rolled 3-segment-only pattern
# would silently miss.
_VIEWER_VERSION_LINE = re.compile(config.XBRL_VIEWER_VERSION_LINE_PATTERN)
_BARE_DATE_LINE = re.compile(r"^[A-Z][a-z]{2}\.\s\d{1,2},\s\d{4}$")
# A 4+-letter ALL-CAPS run immediately followed by a Titlecase word start
# (uppercase, then lowercase) -- a former all-caps header/label directly
# abutting the body text that used to follow it in a different table cell
# or HTML element, e.g. "DISCLOSURESUnaudited" or "LEASESWe". The first
# group is non-greedy so it stops at the shortest run that still lets the
# second group match, rather than swallowing the next word's own leading
# capital (a plain `[A-Z]{4,}` would consume "DISCLOSURESU" whole and the
# match would fail).
#
# Deliberately narrower than "any lowercase immediately followed by
# uppercase" (which would also catch this) -- checked against a real
# false-positive case before choosing this shape: NVDA's own filings
# contain genuine camelCase product names ("GeForce"), which a lowercase-
# to-uppercase rule would incorrectly split ("Ge", "Force"). Requiring an
# ALL-CAPS run before the split point rules that out (confirmed directly:
# re.sub leaves "GeForce RTX and DGX systems" untouched).
#
# The minimum run length is 4, not 2 -- found live against NVDA's own
# "Stock-Based Compensation" section text: a 2-letter minimum matched
# inside "RSUs" and "PSUs" (the acronym fragment "RS"/"PS" is itself a
# 2-letter ALL-CAPS run immediately followed by a Titlecase-shaped "Us"),
# corrupting them into "RS\n\nUs" / "PS\n\nUs". Every real header this
# function needs to catch (DISCLOSURES, FINANCIAL INSTRUMENTS, LEASES,
# COMMITMENTS AND CONTINGENCIES, STOCKHOLDERS' EQUITY) is well over 4
# letters, so raising the floor to 4 loses no real match -- verified
# against the same real sections -- while it stops matching inside RSUs,
# PSUs, and the same-shaped ISOs/IPOs/SPACs (all confirmed left untouched).
_ALL_CAPS_RUN_INTO_TITLECASE = re.compile(r"([A-Z]{4,}?)([A-Z][a-z])")


def clean_section_display_text(raw_text: str, short_name: str) -> str:
    """SPEC-008-batch-4 item 5 (approved 2026-08-16): DISPLAY-TIME cleanup
    of SEC R-file rendering artifacts, exactly like currency escaping --
    the stored text (content-addressed; `section_store`'s hashes feed the
    analysis layer and every cached result keyed off them) is NEVER
    touched. Apply this only at the point section text reaches the
    viewer; the DB row `read_section_text` returns stays exactly what was
    stored.

    Strips, in order, only from the START of the text (never searched for
    deep in the body, so a coincidentally similar phrase in real prose is
    never touched):

    1. A leading viewer version string (`v3.26.1`) on its own line --
       reuses `config.XBRL_VIEWER_VERSION_LINE_PATTERN`, the same pattern
       `section_store.normalize_for_wording_hash` (SPEC-005) already
       strips for its own, unrelated purpose; not a second mechanism.
    2. The duration line (`{short_name} 6 Months Ended` / `12 Months
       Ended` / ...) -- identified by STARTING WITH the section's own
       `short_name`, not by matching a specific duration phrase (10-Ks
       say "12 Months Ended", 10-Qs vary) -- so this doesn't need to
       enumerate every duration phrase SEC filings use.
    3. A bare date line (`Jun. 30, 2026`) immediately after it.
    4. The XBRL abstract-element line (ends in ` [Abstract]`) -- checked
       against the real corpus before writing this: this string does NOT
       always match `short_name` (short_name "Financial Instruments" vs.
       abstract line "Investments, Debt and Equity Securities [Abstract]"
       for the SAME section) so it's identified structurally, by suffix,
       not by content match.
    5. The note's own title, repeated up to a few more times immediately
       before real content starts -- checked against the real corpus
       before writing this: the repeat isn't always title-case-then-ALL-
       CAPS (AMZN's shape, "Accounting Policies and Supplemental
       Disclosures ACCOUNTING POLICIES..."); NVIDIA's own filings repeat
       the SAME-CASE title instead ("Groq Groq...", "Organization and
       Summary of Significant Accounting Policies Organization and
       Summary..."). Strips `short_name` or `short_name.upper()`,
       whichever matches, with or without a trailing space, in a loop --
       general to either shape and to however many times it actually
       repeats, not hard-coded to exactly three.

    Then restores a paragraph break at every remaining ALL-CAPS-run/
    Titlecase join anywhere in the text (see `_ALL_CAPS_RUN_INTO_TITLECASE`
    for why this specific pattern, not a broader lowercase-to-uppercase
    one, was checked and chosen). A join that ISN'T this exact shape --
    e.g. "Stock Repurchase ActivityIn March 2022", a title-case sub-header
    running into title-case content, structurally identical to a genuine
    camelCase brand name -- is deliberately left alone: this project's own
    rule for D15-style findings applies here too, a wrong split is worse
    than an ugly one, and there is no reliable way to tell the two apart
    from the text alone."""
    lines = raw_text.split("\n")
    idx = 0
    if idx < len(lines) and _VIEWER_VERSION_LINE.match(lines[idx].strip()):
        idx += 1
    if idx < len(lines) and lines[idx].startswith(short_name):
        idx += 1
    if idx < len(lines) and _BARE_DATE_LINE.match(lines[idx].strip()):
        idx += 1
    if idx < len(lines) and lines[idx].rstrip().endswith("[Abstract]"):
        idx += 1
    remainder = "\n".join(lines[idx:])

    for _ in range(5):  # a handful of repeats at most; never loops on real content
        stripped = remainder.lstrip()
        if short_name and stripped.startswith(short_name):
            remainder = stripped[len(short_name):]
        elif short_name and stripped.startswith(short_name.upper()):
            remainder = stripped[len(short_name.upper()):]
        else:
            break

    remainder = remainder.lstrip()
    return _ALL_CAPS_RUN_INTO_TITLECASE.sub(r"\1\n\n\2", remainder)


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
