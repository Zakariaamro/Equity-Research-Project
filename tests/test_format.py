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


def test_format_shares_scales_to_millions_no_dollar_sign():
    # SPEC-008-batch-1 render-batch follow-up item 1 (approved 2026-08-11).
    assert fmt.format_shares(10_743_000_000) == "10,743"
    assert fmt.format_shares(685_300_000) == "685"


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
    valid_groups = {
        "Growth", "Margins", "Returns", "Capital & Cash", "Working Capital", "Solvency", "Quality",
        "Income Statement",
    }
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


def test_currency_in_narrative_text_is_escaped():
    # SPEC-008 review D1 (found live, real browser): Streamlit's markdown
    # renders `$...$` as inline LaTeX math mode, swallowing everything
    # between two dollar figures -- Amazon's "$35.0 billion equity
    # commitment ... $20.0 billion financing facility" corrupted exactly
    # this way. A sentence with two `$` figures must survive rendering
    # with both amounts intact and no math-mode markup.
    text = "Amazon secured a $35.0 billion equity commitment and a $20.0 billion financing facility."
    escaped = fmt.escape_markdown_currency(text)
    assert "$35.0 billion equity commitment" in escaped.replace(r"\$", "$")
    assert "$20.0 billion financing facility" in escaped.replace(r"\$", "$")
    assert escaped.count(r"\$") == 2
    assert "$" not in escaped.replace(r"\$", "")  # no bare '$' left to be read as a math delimiter


def test_escape_markdown_currency_is_a_no_op_without_dollar_signs():
    text = "Micron reported net income growth of 12% year over year."
    assert fmt.escape_markdown_currency(text) == text


def test_format_metric_label_adds_unit_only_for_usd():
    # SPEC-008 review D4: every other unit self-labels its own value
    # (51.82%, 3.44x, 156 days); usd is the one exception, since
    # format_usd's value alone is a bare, unlabeled number.
    fcf = config.METRIC_REGISTRY["free_cash_flow"]
    assert fmt.format_metric_label(fcf) == "Free cash flow ($m)"

    gm = config.METRIC_REGISTRY["gross_margin"]
    assert fmt.format_metric_label(gm) == gm.display_name
    assert "$m" not in fmt.format_metric_label(gm)


def test_scale_for_axis_matches_the_single_value_formatters():
    # SPEC-008 review D9: an axis and a tile for the same metric must agree.
    assert fmt.scale_for_axis([0.4517, 0.5182], "percent") == [45.17, 51.82]
    assert fmt.scale_for_axis([-18_171_000_000], "usd") == [-18_171.0]
    assert fmt.scale_for_axis([3.44], "times") == [3.44]  # unscaled, suffix carries the unit instead


def test_axis_ticksuffix_matches_unit_formatters():
    assert fmt.axis_ticksuffix("percent") == "%"
    assert fmt.axis_ticksuffix("times") == "x"
    assert fmt.axis_ticksuffix("days") == " days"
    assert fmt.axis_ticksuffix("usd") == ""  # unit lives in the title ($m), not a per-tick suffix


def test_format_duration_label_maps_period_classes_to_human_phrases():
    # SPEC-008 review D11: "as of <date>" alone doesn't say whether a
    # duration-based figure is a three-month quarter or a nine-month
    # year-to-date cumulative ending the same day.
    assert fmt.format_duration_label("quarterly") == "three-month"
    assert fmt.format_duration_label("three-quarter") == "nine-month"
    assert fmt.format_duration_label("annual") == "FY"
    assert fmt.format_duration_label("instant") == ""  # a balance-sheet figure has no duration to state
    assert fmt.format_duration_label("other") == ""


def test_format_growth_pct_is_always_signed():
    assert fmt.format_growth_pct(0.10) == "+10.0%"
    assert fmt.format_growth_pct(-0.10) == "-10.0%"
    assert fmt.format_growth_pct(0.0) == "+0.0%"


def test_format_growth_pct_empty_string_when_none():
    assert fmt.format_growth_pct(None) == ""


# SPEC-008-batch-4 item 5: display-time cleanup of SEC R-file rendering
# artifacts. Every case below is built from the real shapes confirmed
# against the live database (AMZN 10-Q, NVDA 10-K) before writing the
# function -- not invented edge cases.

def test_clean_section_display_text_strips_leading_version_string():
    raw = "v3.26.1\nAccounting Policies 6 Months Ended\nJun. 30, 2026\nAccounting Policies [Abstract]\nAccounting Policies ACCOUNTING POLICIES\nReal content starts here."
    cleaned = fmt.clean_section_display_text(raw, "Accounting Policies")
    assert "v3.26.1" not in cleaned
    assert cleaned.startswith("Real content starts here.")


def test_clean_section_display_text_strips_duration_and_date_and_abstract_lines():
    raw = "v1.0.0\nLeases 3 Months Ended\nMar. 31, 2026\nLeases [Abstract]\nLeases LEASES\nWe have entered into leases."
    cleaned = fmt.clean_section_display_text(raw, "Leases")
    assert "3 Months Ended" not in cleaned
    assert "Mar. 31, 2026" not in cleaned
    assert "[Abstract]" not in cleaned
    assert cleaned.startswith("We have entered into leases.")


def test_clean_section_display_text_strips_abstract_line_even_when_it_does_not_match_short_name():
    # Checked live: the abstract line's own text does not always match
    # short_name (e.g. short_name "Financial Instruments" vs. abstract
    # line "Investments, Debt and Equity Securities [Abstract]") -- must
    # be identified structurally, by the "[Abstract]" suffix, not by
    # content match.
    raw = (
        "v3.26.1\nFinancial Instruments 6 Months Ended\nJun. 30, 2026\n"
        "Investments, Debt and Equity Securities [Abstract]\n"
        "Financial Instruments FINANCIAL INSTRUMENTS\nCash and equivalents were as follows."
    )
    cleaned = fmt.clean_section_display_text(raw, "Financial Instruments")
    assert "[Abstract]" not in cleaned
    assert cleaned.startswith("Cash and equivalents were as follows.")


def test_clean_section_display_text_strips_title_case_then_all_caps_repeat():
    # AMZN's shape.
    raw = (
        "v3.26.1\nAccounting Policies and Supplemental Disclosures 6 Months Ended\n"
        "Jun. 30, 2026\nAccounting Policies [Abstract]\n"
        "Accounting Policies and Supplemental Disclosures ACCOUNTING POLICIES AND SUPPLEMENTAL "
        "DISCLOSURESUnaudited Interim Financial Information follows."
    )
    cleaned = fmt.clean_section_display_text(raw, "Accounting Policies and Supplemental Disclosures")
    assert "ACCOUNTING POLICIES" not in cleaned
    assert cleaned.startswith("Unaudited Interim Financial Information follows.")


def test_clean_section_display_text_strips_same_case_doubled_title_repeat():
    # NVDA's shape -- the title repeats verbatim rather than title-case-
    # then-ALL-CAPS.
    raw = "v3.26.1\nGroq 3 Months Ended\nApr. 26, 2026\nGroq [Abstract]\nGroq Groq Real content about the acquisition follows."
    cleaned = fmt.clean_section_display_text(raw, "Groq")
    assert cleaned.startswith("Real content about the acquisition follows.")


def test_clean_section_display_text_restores_paragraph_break_at_all_caps_header_join():
    # short_name deliberately doesn't match the embedded ALL-CAPS run, so
    # this exercises the paragraph-break regex itself rather than the
    # title-repeat stripping loop (which would otherwise consume
    # "DISCLOSURES" as a repeat of a matching title first).
    raw = "COMMITMENTSAndContingencies were as follows for the period."
    cleaned = fmt.clean_section_display_text(raw, "Unrelated Section Name")
    assert "COMMITMENTSAndContingencies" not in cleaned
    assert "COMMITMENTS\n\nAndContingencies were as follows for the period." in cleaned


def test_clean_section_display_text_does_not_split_camelcase_brand_names():
    # NVDA's own filings contain genuine camelCase product names --
    # "GeForce" must never be split into "Ge" / "Force".
    raw = "GeForce RTX and DGX systems drove datacenter revenue growth."
    cleaned = fmt.clean_section_display_text(raw, "Products")
    assert "GeForce" in cleaned
    assert "Ge\n\nForce" not in cleaned


def test_clean_section_display_text_does_not_split_short_acronym_plurals():
    # The bug found live in NVDA's "Stock-Based Compensation" section: a
    # 2-letter ALL-CAPS-run minimum matched inside "RSUs"/"PSUs" ("RS"/
    # "PS" being themselves a short all-caps run followed by a
    # Titlecase-shaped "Us"), corrupting them into "RS\n\nUs"/"PS\n\nUs".
    raw = (
        "We recognize stock-based compensation expense from grants of restricted "
        "stock units, or RSUs, performance stock units, or PSUs, and market-based "
        "PSUs, and issuances under our employee stock purchase plan, or ESPP."
    )
    cleaned = fmt.clean_section_display_text(raw, "Stock-Based Compensation")
    assert "RS\n\nUs" not in cleaned
    assert "PS\n\nUs" not in cleaned
    assert "RSUs" in cleaned
    assert "PSUs" in cleaned


def test_clean_section_display_text_does_not_split_other_short_acronyms():
    raw = "Grants of ISOs and RSUs are common; our IPOs and SPACs this year were unrelated."
    cleaned = fmt.clean_section_display_text(raw, "Equity")
    for acronym in ("ISOs", "RSUs", "IPOs", "SPACs"):
        assert acronym in cleaned


def test_clean_section_display_text_never_modifies_the_stored_text_object():
    # The hard constraint: this function must be side-effect-free on its
    # input -- the stored, content-addressed row is never touched, only
    # what's handed back for display.
    raw = "v3.26.1\nLeases 3 Months Ended\nMar. 31, 2026\nLeases [Abstract]\nLeases LEASES\nBody text."
    original = str(raw)
    fmt.clean_section_display_text(raw, "Leases")
    assert raw == original


def test_clean_section_display_text_leaves_titlecase_to_titlecase_joins_alone():
    # Deliberate limitation, stated in the item itself: a join that isn't
    # an ALL-CAPS-run-into-Titlecase is structurally indistinguishable
    # from a genuine camelCase brand name using text alone -- a wrong
    # split is worse than an ugly one, so these are left untouched.
    raw = "Stock Repurchase ActivityIn March 2022, the Board authorized a program."
    cleaned = fmt.clean_section_display_text(raw, "Stockholders' Equity")
    assert "Stock Repurchase ActivityIn March 2022" in cleaned


# SPEC-008-batch-4 follow-up item 2 (approved 2026-08-18): a full stop
# directly into a capital letter, no space -- always a lost boundary,
# except inside a multi-period initialism like "U.S."/"B.V.", which this
# rule must not corrupt. Every case below is either a real example from
# the item's own message or a shape confirmed live against the real
# corpus before this was written.

def test_clean_section_display_text_restores_paragraph_break_at_period_into_capital():
    raw = "eliminated.Use of estimates in the preparation of financial statements."
    cleaned = fmt.clean_section_display_text(raw, "Unrelated Section Name")
    assert "eliminated.Use" not in cleaned
    assert "eliminated.\n\nUse of estimates" in cleaned


def test_clean_section_display_text_restores_paragraph_break_at_period_into_capital_second_example():
    raw = "estimates.During the period, management made no material changes."
    cleaned = fmt.clean_section_display_text(raw, "Unrelated Section Name")
    assert "estimates.During" not in cleaned
    assert "estimates.\n\nDuring the period" in cleaned


def test_clean_section_display_text_restores_paragraph_break_after_a_form_reference():
    # "10-K." ends in a single capital letter but is a 4-character code,
    # not a bare initial -- the digit-hyphen prefix keeps it out of the
    # single-letter exclusion built for "U.S."-style abbreviations.
    # Confirmed live: this exact shape repeats across the real corpus
    # ("...our 2025 Annual Report on Form 10-K.Principles of...").
    raw = "our 2025 Annual Report on Form 10-K.Principles of consolidation follow."
    cleaned = fmt.clean_section_display_text(raw, "Unrelated Section Name")
    assert "10-K.Principles" not in cleaned
    assert "10-K.\n\nPrinciples of consolidation" in cleaned


def test_clean_section_display_text_does_not_split_us_style_initials():
    # The false positive found live testing the naive version of this
    # rule against the real corpus: "U.S." is "U" + "." + "S" + ".", the
    # identical local shape (single capital, period, no space) to a
    # genuine one-letter sentence ending -- roughly 2,700 real
    # occurrences in the corpus, by far the most common risk case.
    raw = "Our income tax expense includes U.S. and foreign components for the period."
    cleaned = fmt.clean_section_display_text(raw, "Unrelated Section Name")
    assert "U.S. and foreign components" in cleaned
    assert "U.\n\nS." not in cleaned


def test_clean_section_display_text_does_not_split_other_multi_period_initials():
    # B.V. (Dutch corporate form), K.K. (Japanese), and U.S. federal
    # district short forms (E.D./W.D./N.D.) -- all confirmed live in the
    # real corpus, all the same "single capital + period, no space" shape.
    raw = 'Micron Semiconductor B.V., ("Micron B.V."), in the U.S. District Court for the W.D. of Texas.'
    cleaned = fmt.clean_section_display_text(raw, "Unrelated Section Name")
    assert "B.V." in cleaned
    assert "B.\n\nV." not in cleaned
    assert "W.D." in cleaned
    assert "W.\n\nD." not in cleaned


def test_clean_section_display_text_does_not_split_hyphenated_us_abbreviation():
    # Found live: a bare single-letter exclusion is not enough on its own
    # -- "non-U.S." would otherwise merge "non-U" into one 5-character
    # token via the hyphen and escape the exclusion. Must still resolve
    # to the bare single letter "U" for this specific case.
    raw = "certain non-U.S. subsidiaries had cumulative earnings not subject to tax."
    cleaned = fmt.clean_section_display_text(raw, "Unrelated Section Name")
    assert "non-U.S. subsidiaries" in cleaned
    assert "non-U.\n\nS." not in cleaned
