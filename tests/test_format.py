"""Tests for dashboard.format (SPEC-008 R1). Pure functions -- no Streamlit,
no database, no fixtures needed beyond config.METRIC_REGISTRY entries."""

from __future__ import annotations

import pytest

from dashboard import format as fmt
from edgar import config


def test_format_percent_precision():
    assert fmt.format_percent(0.5029, 1) == "50.3%"
    assert fmt.format_percent(-0.9, 1) == "-90.0%"
    assert fmt.format_percent(0.03224, 2) == "3.22%"


def test_format_usd_scaling():
    # R1: ALL usd values render in millions, everywhere -- 637.96 billion -> 637,960.
    assert fmt.format_usd(637_960_000_000, 0) == "637,960"
    # A $1.3bn impairment -> 1,300.
    assert fmt.format_usd(1_300_000_000, 0) == "1,300"
    assert fmt.format_usd(-18_171_000_000, 0) == "-18,171"


def test_format_usd_per_share_stays_in_dollars():
    # R1's stated exception -- never millions for per-share values.
    assert fmt.format_usd_per_share(0.10, 2) == "$0.10"
    assert fmt.format_usd_per_share(-1.5) == "$-1.50"


def test_format_days():
    assert fmt.format_days(35, 0) == "35 days"
    assert fmt.format_days(132.7376, 0) == "133 days"


def test_format_ratio_and_times():
    assert fmt.format_ratio(-2.6121, 2) == "-2.61"
    assert fmt.format_times(3.4442, 2) == "3.44x"


def test_format_null_returns_not_available_never_zero():
    assert fmt.format_null(None) == "Not available"
    assert "Not available" in fmt.format_null("borrowings could not be resolved for this period")
    assert fmt.format_null(None) != "0"
    assert fmt.format_null(None) != ""


def test_format_metric_value_dispatches_by_unit():
    gm = config.METRIC_REGISTRY["gross_margin"]
    assert fmt.format_metric_value(0.50, gm) == "50.0%"

    fcf = config.METRIC_REGISTRY["free_cash_flow"]
    assert fmt.format_metric_value(-18_171_000_000, fcf) == "-18,171"

    ac = config.METRIC_REGISTRY["asset_turnover"]
    assert fmt.format_metric_value(0.8764, ac) == "0.88x"

    days = config.METRIC_REGISTRY["days_payables"]
    assert fmt.format_metric_value(156.5, days) == "156 days"


def test_format_metric_value_null_never_zero_or_blank():
    gm = config.METRIC_REGISTRY["gross_margin"]
    result = fmt.format_metric_value(None, gm, null_reason="borrowings unavailable")
    assert result != "0"
    assert result != ""
    assert "Not available" in result
    assert "borrowings unavailable" in result


def test_format_metric_value_rejects_unrecognised_unit():
    from dataclasses import replace
    bad = replace(config.METRIC_REGISTRY["gross_margin"], unit="furlongs")
    with pytest.raises(ValueError):
        fmt.format_metric_value(0.5, bad)


def test_metric_registry_display_fields_present_for_every_metric():
    """A metric without display metadata should fail loudly rather than
    render as a raw identifier -- checked at config.py import time already
    (_validate_metric_display_metadata); this test pins the same invariant
    from the dashboard's own test suite."""
    valid_units = {"percent", "usd", "usd_per_share", "days", "ratio", "times"}
    valid_groups = {"Growth", "Margins", "Returns", "Capital & Cash", "Working Capital", "Solvency", "Quality"}
    for name, mdef in config.METRIC_REGISTRY.items():
        assert mdef.display_name, f"{name} has no display_name"
        assert mdef.unit in valid_units, f"{name} has invalid unit {mdef.unit!r}"
        assert mdef.group in valid_groups, f"{name} has invalid group {mdef.group!r}"
        assert mdef.description, f"{name} has no description"


def test_format_period_label_distinguishes_annual_from_quarterly():
    annual = fmt.format_period_label("2025-12-31", "annual")
    quarterly = fmt.format_period_label("2026-03-31", "quarterly")
    assert "FY" in annual
    assert "FY" not in quarterly
    assert "2025-12-31" not in annual  # rendered as a human date, not raw ISO
    assert "Dec" in annual


def test_format_severity_label_never_a_color():
    assert fmt.format_severity_label("high") == "High"
    assert fmt.format_severity_label("medium") == "Medium"
    assert fmt.format_severity_label("low") == "Low"
    for label in (fmt.format_severity_label("high"), fmt.format_severity_label("medium"), fmt.format_severity_label("low")):
        assert not label.startswith("#")  # never a hex color code standing in for a label


def test_format_date_handles_iso_string():
    assert fmt.format_date("2026-02-06") == "Feb 6, 2026"
    assert fmt.format_date(None) == fmt.NOT_AVAILABLE
