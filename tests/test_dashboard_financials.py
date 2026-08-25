"""Tests for dashboard.app_pages.financials (SPEC-008-batch-1 render-batch
follow-up, approved 2026-08-11; renamed by SPEC-008-batch-2 item 2,
approved 2026-08-13). Found live: item 6 built data.get_eps_and_shares_table
but never added it to _STATEMENT_TABLE_FUNCS, so the rows existed in the
data layer with no display path at all -- this pins the tab dict directly
so a future statement can't go missing the same way without a test
failing."""

from __future__ import annotations

from dashboard import data


def test_statement_table_funcs_includes_key_metrics():
    from dashboard.app_pages import financials

    assert financials._STATEMENT_TABLE_FUNCS["Key metrics"] is data.get_key_metrics_table


def test_statement_table_funcs_includes_all_four_statements():
    from dashboard.app_pages import financials

    assert set(financials._STATEMENT_TABLE_FUNCS.keys()) == {
        "Income statement", "Key metrics", "Balance sheet", "Cash flow",
    }
