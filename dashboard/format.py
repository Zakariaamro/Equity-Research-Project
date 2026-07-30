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
    impact is not 0.0000001 of anything useful."""
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


def format_severity_label(severity: str) -> str:
    """R8: severity is conveyed by an explicit text label and by ordering,
    never by colour. This is the one place that decides the label text, so
    every page reads the same word for the same severity."""
    return {"high": "High", "medium": "Medium", "low": "Low"}.get(severity, severity.title())
