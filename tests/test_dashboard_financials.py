"""Tests for dashboard.pages.financials (SPEC-008-batch-1 render-batch
follow-up, approved 2026-08-11). Found live: item 6 built
data.get_eps_and_shares_table but never added it to _STATEMENT_TABLE_FUNCS,
so the rows existed in the data layer with no display path at all -- this
pins the tab dict directly so a future statement can't go missing the same
way without a test failing."""

from __future__ import annotations

from dashboard import data


def test_statement_table_funcs_includes_eps_and_shares():
    from dashboard.pages import financials

    assert financials._STATEMENT_TABLE_FUNCS["EPS & shares"] is data.get_eps_and_shares_table


def test_statement_table_funcs_includes_all_four_statements():
    from dashboard.pages import financials

    assert set(financials._STATEMENT_TABLE_FUNCS.keys()) == {
        "Income statement", "EPS & shares", "Balance sheet", "Cash flow",
    }
