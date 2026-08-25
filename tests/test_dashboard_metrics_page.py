"""Tests for dashboard.app_pages.metrics (SPEC-008 C6). `_group_metrics` is the
one function responsible for AC5's guarantee -- a metric added under a new
category appears with no page-file changes. A hardcoded category list would
make that guarantee silently false; these pin it against the registry
directly, without needing a full page render.

SPEC-008 C6/AC5 (2026-08-04): a throwaway metric was added live to
METRIC_REGISTRY under a brand-new category ("Demonstration", not one of the
seven existing ones), confirmed via AppTest to produce an eighth Metrics-page
tab with zero changes to pages/metrics.py, then removed. This module locks
that guarantee in permanently, without needing to touch the real registry."""

from __future__ import annotations

from dataclasses import replace

from edgar import config


def _metric_def_with_group(group: str) -> config.MetricDef:
    template = config.METRIC_REGISTRY["gross_margin"]
    return replace(template, name="test_throwaway_metric", group=group)


def test_group_metrics_derives_categories_from_the_registry_not_a_hardcoded_list():
    from dashboard.app_pages import metrics

    extra = _metric_def_with_group("Zzz Brand New Category")
    original = dict(config.METRIC_REGISTRY)
    config.METRIC_REGISTRY["test_throwaway_metric"] = extra
    try:
        groups = metrics._group_metrics()
        assert "Zzz Brand New Category" in groups
        assert groups["Zzz Brand New Category"] == ["test_throwaway_metric"]
    finally:
        config.METRIC_REGISTRY.clear()
        config.METRIC_REGISTRY.update(original)


def test_group_metrics_preserves_registry_order_not_alphabetical_or_hardcoded():
    from dashboard.app_pages import metrics

    groups = metrics._group_metrics()
    # The pre-fix hardcoded list was exactly the first seven of this
    # sequence -- confirms the registry-derived order still matches it
    # today, not by coincidence but because dict.setdefault preserves
    # first-appearance order while walking METRIC_REGISTRY, which is
    # itself organized by category. "Income Statement" (SPEC-008 D12,
    # 2026-08-08) landing between "Capital & Cash" and "Working Capital"
    # is exactly this mechanism doing its job on a second, real category
    # addition -- not just the throwaway one demonstrated below.
    assert list(groups.keys()) == [
        "Growth", "Margins", "Returns", "Capital & Cash", "Income Statement", "Working Capital", "Solvency",
        "Quality",
    ]


def test_group_metrics_new_category_appears_after_existing_ones_when_appended():
    from dashboard.app_pages import metrics

    extra = _metric_def_with_group("Zzz Brand New Category")
    original = dict(config.METRIC_REGISTRY)
    config.METRIC_REGISTRY["test_throwaway_metric"] = extra
    try:
        groups = metrics._group_metrics()
        # Appended to METRIC_REGISTRY last -> its category is the last key,
        # not silently dropped and not requiring a page-file change to show.
        assert list(groups.keys())[-1] == "Zzz Brand New Category"
    finally:
        config.METRIC_REGISTRY.clear()
        config.METRIC_REGISTRY.update(original)
