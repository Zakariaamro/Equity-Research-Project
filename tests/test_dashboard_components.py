"""SPEC-008 review, post-D1: rendering-path tests for components.py's
narrative renderers. The unit test on `format.escape_markdown_currency`
proves the helper works in isolation; it does not prove any given render
function actually calls it -- drop the call from `brief_sentence` and that
test still passes. These run the real function through `AppTest` (headless
Streamlit) and assert on what actually reaches a markdown-parsed element:
no bare '$' should survive, since an unescaped pair is what Streamlit's
markdown renders as LaTeX math-mode delimiters (SPEC-008 review D1)."""

from __future__ import annotations

import pandas as pd
from streamlit.testing.v1 import AppTest

from dashboard import components
from dashboard import format as fmt_module


def _rendered_texts(at: AppTest) -> list[str]:
    return [m.value for m in at.markdown] + [c.value for c in at.caption]


def _assert_no_bare_dollar(texts: list[str]) -> None:
    for text in texts:
        assert "$" not in text.replace(r"\$", ""), f"unescaped '$' reached Streamlit: {text!r}"


def test_brief_sentence_escapes_currency_through_the_real_render_path():
    def script(sentence):
        from dashboard import components

        components.brief_sentence(sentence)

    sentence = {
        "sentence_type": "restatement",
        "text": "Amazon secured a $35.0 billion equity commitment and a $20.0 billion financing facility.",
        "sources": [
            {"severity": "high", "text": "The note discloses $1.0 billion and $2.0 billion separately."},
        ],
    }
    at = AppTest.from_function(script, kwargs={"sentence": sentence})
    at.run()
    texts = _rendered_texts(at)
    assert any("35.0 billion" in t and "20.0 billion" in t for t in texts)
    _assert_no_bare_dollar(texts)


def test_brief_sentence_with_zero_sources_has_no_caret_at_all():
    # SPEC-008 C2 v3 (2026-08-04 -- ARCHITECTURE.md decision log has the
    # full sequence: v1's visible count line, v2's raw-HTML caret rejected
    # after failing live twice, this v3 native-Streamlit caret): a sentence
    # with NO sources renders as a single, un-columned line with no popover
    # at all -- its absence, next to every other sentence's caret, is what
    # makes it conspicuous.
    def script(sentence):
        from dashboard import components

        components.brief_sentence(sentence)

    sentence = {"sentence_type": "restatement", "text": "A sentence with no sources.", "sources": []}
    at = AppTest.from_function(script, kwargs={"sentence": sentence})
    at.run()
    markdown_values = [m.value for m in at.markdown]
    assert any(v == "A sentence with no sources." for v in markdown_values)
    assert not list(at.columns)
    assert not at.get("popover")


def test_brief_sentence_with_sources_renders_a_popover_caret_carrying_the_count():
    def script(sentence):
        from dashboard import components

        components.brief_sentence(sentence)

    sentence = {
        "sentence_type": "juxtaposition",
        "text": "A sentence with three sources.",
        "sources": [
            {"severity": "high", "text": "source one"},
            {"severity": "medium", "text": "source two"},
            {"severity": "low", "text": "source three"},
        ],
    }
    at = AppTest.from_function(script, kwargs={"sentence": sentence})
    at.run()
    assert any(m.value == "A sentence with three sources." for m in at.markdown)
    popovers = at.get("popover")
    assert len(popovers) == 1
    # Confirmed live: st.popover already renders its own disclosure
    # chevron -- an explicit label doubled it up. The label stays empty;
    # the widget's own indicator is the entire visible caret.
    assert popovers[0].proto.popover.label == ""
    assert popovers[0].proto.popover.help == "3 sources"  # the count, a hover tooltip, not a visible line
    caption_values = [c.value for c in popovers[0].caption]
    assert any("source one" in c for c in caption_values)
    assert any("source two" in c for c in caption_values)
    assert any("source three" in c for c in caption_values)


def test_brief_sentence_display_drops_the_sentence_type_prefix():
    # SPEC-008 C2: [restatement]/[juxtaposition]/[grouping] is display-only
    # scaffolding, dropped here -- sentence_type itself stays in the
    # database unchanged (SPEC-007 R4 dispatches verification on it;
    # ROADMAP-V2 measures the type distribution from it).
    def script(sentence):
        from dashboard import components

        components.brief_sentence(sentence)

    sentence = {"sentence_type": "restatement", "text": "Plain sentence text.", "sources": []}
    at = AppTest.from_function(script, kwargs={"sentence": sentence})
    at.run()
    markdown_values = [m.value for m in at.markdown]
    assert any(v == "Plain sentence text." for v in markdown_values)
    assert not any("[restatement]" in v for v in markdown_values)


def test_finding_item_escapes_currency_through_the_real_render_path():
    def script(finding):
        from dashboard import components

        components.finding_item(finding)

    finding = {
        "severity": "high",
        "category": "liquidity",
        "headline": "Two commitments disclosed: $425 million and $20 million.",
        "detail": "Detail also cites $1 million and $2 million.",
        "quote": "The quote itself references $3 million and $4 million in damages.",
    }
    at = AppTest.from_function(script, kwargs={"finding": finding})
    at.run()
    texts = _rendered_texts(at)
    assert any("425 million" in t and "20 million" in t for t in texts)
    _assert_no_bare_dollar(texts)


def test_observation_item_escapes_currency_through_the_real_render_path():
    def script(obs):
        from dashboard import components

        components.observation_item(obs)

    obs = {
        "severity": "medium",
        "statement": "Inventory rose $100 million while revenue fell $50 million.",
    }
    at = AppTest.from_function(script, kwargs={"obs": obs})
    at.run()
    texts = _rendered_texts(at)
    assert any("100 million" in t and "50 million" in t for t in texts)
    _assert_no_bare_dollar(texts)


def test_null_metric_reason_is_reachable_in_full():
    # SPEC-008 review D3: `st.metric`'s value string is truncated to the
    # tile's column width -- a NULL reason rendered there (satisfying AC9's
    # letter) was unreachable in practice. The full reason must appear
    # somewhere in the rendered output, untruncated.
    def script(label, null_reason):
        from dashboard import components

        components.null_metric_tile(label, null_reason)

    long_reason = "no data computed for this company/period because the underlying XBRL concept was never tagged in any filing on record"
    at = AppTest.from_function(script, kwargs={"label": "Free cash flow ($m)", "null_reason": long_reason})
    at.run()
    all_text = [m.value for m in at.metric] + [c.value for c in at.caption]
    assert any(long_reason in t for t in all_text)


def test_null_metric_tile_caption_does_not_repeat_not_available():
    # Reported live: value read "Not available", caption read "Not
    # available -- capex missing" -- the caption repeated a fact the
    # metric's own value already stated. The caption should carry only the
    # reason.
    def script(label, null_reason):
        from dashboard import components

        components.null_metric_tile(label, null_reason)

    at = AppTest.from_function(script, kwargs={"label": "Capex to revenue", "null_reason": "capex missing"})
    at.run()
    assert at.metric[0].value == "Not available"
    captions = [c.value for c in at.caption]
    assert "capex missing" in captions
    assert not any("Not available" in c for c in captions)


def test_null_metric_reason_is_reachable_when_the_row_exists_with_a_null_value():
    # SPEC-008 review D3, second null path (found live, second review pass):
    # `metric_tile`'s `latest is None` branch (a row was never computed at
    # all) was fixed first, but Micron's actual tiles hit a DIFFERENT
    # branch -- a row EXISTS with `value=None` and a `null_reason`.
    # `format_metric_value` used to fold that into one string straight into
    # `st.metric`, truncated exactly like the other path. This test covers
    # the row-exists case the first D3 test did not.
    def script(metric_def, latest):
        from dashboard import components

        components.metric_tile(metric_def, latest)

    from edgar import config

    mdef = config.METRIC_REGISTRY["roic"]
    long_reason = "cfo missing because the debt-tag resolution gap leaves this quarter's cash flow statement unresolved"
    latest = {"value": None, "null_reason": long_reason, "period_end": "2026-05-28"}
    at = AppTest.from_function(script, kwargs={"metric_def": mdef, "latest": latest})
    at.run()
    all_text = [m.value for m in at.metric] + [c.value for c in at.caption]
    assert any(long_reason in t for t in all_text)
    assert any(m.value == fmt_module.NOT_AVAILABLE for m in at.metric)


def test_beneish_m_score_tile_shows_its_flag_threshold():
    # SPEC-008 review D7: a bare "-2.75" means nothing without the
    # conventional threshold it is measured against.
    def script(metric_def, latest):
        from dashboard import components

        components.metric_tile(metric_def, latest)

    from edgar import config

    mdef = config.METRIC_REGISTRY["beneish_m_score"]
    latest = {"value": -2.61, "period_end": "2026-02-06", "null_reason": None}
    at = AppTest.from_function(script, kwargs={"metric_def": mdef, "latest": latest})
    at.run()
    captions = [c.value for c in at.caption]
    assert any("-1.78" in c for c in captions)


def test_metric_tile_omits_threshold_caption_when_none_is_set():
    def script(metric_def, latest):
        from dashboard import components

        components.metric_tile(metric_def, latest)

    from edgar import config

    mdef = config.METRIC_REGISTRY["gross_margin"]
    assert mdef.flag_threshold is None
    latest = {"value": 0.42, "period_end": "2026-02-06", "null_reason": None}
    at = AppTest.from_function(script, kwargs={"metric_def": mdef, "latest": latest})
    at.run()
    captions = [c.value for c in at.caption]
    assert not any("threshold" in c.lower() for c in captions)


def test_metric_tile_bolds_the_caption_when_its_period_differs_from_the_anchor():
    # SPEC-008 review D8: Micron's cash row showed an annual-basis tile
    # nine months older than its quarterly-basis neighbours, labelled
    # correctly but at the same type size and weight -- easy to misread as
    # comparable. Weight (bold), not colour, marks the mismatch.
    def script(metric_def, latest, anchor_period_end):
        from dashboard import components

        components.metric_tile(metric_def, latest, anchor_period_end=anchor_period_end)

    from edgar import config

    mdef = config.METRIC_REGISTRY["free_cash_flow"]
    latest = {"value": -18_171_000_000, "period_end": "2025-08-28", "null_reason": None}
    at = AppTest.from_function(
        script, kwargs={"metric_def": mdef, "latest": latest, "anchor_period_end": "2026-05-28"}
    )
    at.run()
    captions = [c.value for c in at.caption]
    assert any(c.startswith("**") and "different period" in c for c in captions)


def test_chart_series_color_assigns_three_distinct_colors_for_the_real_watchlist():
    # SPEC-008-batch-4 item 4 (approved 2026-08-16): AMZN and MU were
    # rendering in near-identical blues under Plotly's own default
    # sequence (no colour was ever set here before this item).
    from dashboard import components

    colors = {t: components._chart_series_color(t) for t in ("AMZN", "NVDA", "MU")}
    assert len(set(colors.values())) == 3


def test_chart_series_color_is_a_pure_function_of_the_ticker_alone():
    # A company keeps the SAME colour whether it's shown alongside all
    # three others or alone -- true by construction here (the function
    # takes only `ticker`, nothing about which others are also being
    # charted), asserted directly rather than left implicit.
    from dashboard import components

    assert components._chart_series_color("MU") == components._chart_series_color("MU")
    solo_amzn = components._chart_series_color("AMZN")
    assert solo_amzn == components._chart_series_color("AMZN")


def test_metric_chart_renders_three_genuinely_distinguishable_series_colors():
    import json

    from edgar import config

    def script(metric_def, series_by_ticker, cik_by_ticker):
        from dashboard import components

        components.metric_chart(metric_def, series_by_ticker, cik_by_ticker, key_prefix="t")

    mdef = config.METRIC_REGISTRY["gross_margin"]
    series_by_ticker = {
        "AMZN": [{"period_end": "2025-03-31", "period_start": "2025-01-01", "value": 0.5, "null_reason": None}],
        "NVDA": [{"period_end": "2025-03-31", "period_start": "2025-01-01", "value": 0.6, "null_reason": None}],
        "MU": [{"period_end": "2025-03-31", "period_start": "2025-01-01", "value": 0.4, "null_reason": None}],
    }
    cik_by_ticker = {"AMZN": "0001018724", "NVDA": "0001045810", "MU": "0000723125"}
    at = AppTest.from_function(
        script, kwargs={"metric_def": mdef, "series_by_ticker": series_by_ticker, "cik_by_ticker": cik_by_ticker}
    )
    at.run()
    assert at.exception == []
    chart = at.get("plotly_chart")[0]
    spec = json.loads(chart.proto.spec)
    colors_by_name = {trace["name"]: trace["line"]["color"] for trace in spec["data"]}
    assert len(colors_by_name) == 3
    assert len(set(colors_by_name.values())) == 3  # all three genuinely distinct, not two matching


def test_observation_ids_cited_in_brief_extracts_observation_refs_only():
    # SPEC-008 review D10: the same observation must not appear verbatim in
    # both "The brief" and "What changed?" -- this is the set the caller
    # (overview.py) excludes from "What changed?". Finding sources are a
    # different kind and must not leak into this set. Uses the shape
    # data.get_brief_sentences actually returns: resolved `sources`, each
    # carrying `kind` ("observation" | "finding") and the full `row`.
    from dashboard import components

    sentences = [
        {
            "sources": [
                {"kind": "observation", "row": {"id": 1}},
                {"kind": "finding", "row": {"id": 99}},
            ]
        },
        {"sources": [{"kind": "observation", "row": {"id": 2}}]},
        {"sources": []},
    ]
    assert components.observation_ids_cited_in_brief(sentences) == {1, 2}


def test_metric_tile_caption_is_plain_when_its_period_matches_the_anchor():
    def script(metric_def, latest, anchor_period_end):
        from dashboard import components

        components.metric_tile(metric_def, latest, anchor_period_end=anchor_period_end)

    from edgar import config

    mdef = config.METRIC_REGISTRY["free_cash_flow"]
    latest = {"value": -18_171_000_000, "period_end": "2026-05-28", "null_reason": None}
    at = AppTest.from_function(
        script, kwargs={"metric_def": mdef, "latest": latest, "anchor_period_end": "2026-05-28"}
    )
    at.run()
    captions = [c.value for c in at.caption]
    assert not any("different period" in c for c in captions)


# --- environment visibility ---


def test_environment_caption_shows_the_running_streamlit_version(monkeypatch):
    import streamlit as st

    monkeypatch.setattr(st, "__version__", "1.60.0")

    def script():
        from dashboard import components

        components.environment_caption()

    at = AppTest.from_function(script)
    at.run()
    assert at.exception == []
    captions = [c.value for c in at.sidebar.caption]
    assert any("1.60.0" in c for c in captions)
    assert not at.sidebar.warning


def test_environment_caption_warns_below_this_projects_declared_floor(monkeypatch):
    # SPEC-008 review (found live, C4 rebuild): a bare `streamlit` on PATH
    # resolved to Anaconda's 1.51.0, not this project's `.venv` -- exactly
    # this project's declared floor, which is why the check compares
    # against the floor rather than against whatever `.venv` happens to
    # have installed.
    import streamlit as st

    monkeypatch.setattr(st, "__version__", "1.40.0")

    def script():
        from dashboard import components

        components.environment_caption()

    at = AppTest.from_function(script)
    at.run()
    assert at.exception == []
    warnings = [w.value for w in at.sidebar.warning]
    assert any("1.40.0" in w and "below" in w for w in warnings)


# --- SPEC-009 Part B (approved 2026-08-25): "the deployed app must show
# what filing it's current as of, on every page" ---


def test_data_freshness_caption_states_ticker_form_type_and_both_dates(monkeypatch):
    from dashboard import data

    monkeypatch.setattr(
        data, "get_most_recent_filing",
        lambda: {
            "accession_no": "acc1", "ticker": "AMZN", "form_type": "10-Q",
            "filing_date": "2026-08-21", "discovered_at": "2026-08-24T06:00:00",
        },
    )

    def script():
        from dashboard import components

        components.data_freshness_caption()

    at = AppTest.from_function(script)
    at.run()
    assert at.exception == []
    captions = [c.value for c in at.sidebar.caption]
    assert any("AMZN" in c and "10-Q" in c for c in captions)
    # Both dates present and distinguishable -- filed vs. discovered are a
    # real, load-bearing distinction (get_most_recent_filing's own
    # docstring), not interchangeable phrasing.
    assert any("Aug 21, 2026" in c for c in captions)
    assert any("Aug 24, 2026" in c for c in captions)


def test_data_freshness_caption_states_the_empty_case_explicitly(monkeypatch):
    from dashboard import data

    monkeypatch.setattr(data, "get_most_recent_filing", lambda: None)

    def script():
        from dashboard import components

        components.data_freshness_caption()

    at = AppTest.from_function(script)
    at.run()
    assert at.exception == []
    captions = [c.value for c in at.sidebar.caption]
    # R5/R8 discipline (dashboard/format.py's own NULL rule) applied here
    # too -- never blank, never silently omitted.
    assert any("no filings" in c.lower() for c in captions)


# --- C5: shared sub-tab bar ---


def test_sub_tab_bar_renders_options_horizontally_and_defaults_to_the_first():
    def script():
        from dashboard import components

        selected = components.sub_tab_bar("demo", "Category", ["Alpha", "Beta", "Gamma"])
        import streamlit as st

        st.write(f"selected: {selected}")

    at = AppTest.from_function(script)
    at.run()
    radios = [r for r in at.radio if r.key == "sub_tab__demo"]
    assert len(radios) == 1
    assert radios[0].options == ["Alpha", "Beta", "Gamma"]
    assert any(m.value == "selected: Alpha" for m in at.markdown)


def test_sub_tab_bar_selection_survives_an_unrelated_rerun():
    # SPEC-008 C5: sub-tab selection must survive company switching the way
    # the sidebar company selector already survives page switching (R4a).
    # Simulated here as a full rerun triggered by a DIFFERENT widget --
    # exactly what a sidebar multiselect change looks like from this
    # widget's point of view.
    def script():
        from dashboard import components

        components.sub_tab_bar("demo", "Category", ["Alpha", "Beta", "Gamma"])
        import streamlit as st

        st.checkbox("unrelated", key="unrelated_widget")

    at = AppTest.from_function(script)
    at.run()
    at.radio(key="sub_tab__demo").set_value("Gamma").run()
    assert at.radio(key="sub_tab__demo").value == "Gamma"
    at.checkbox(key="unrelated_widget").set_value(True).run()
    assert at.radio(key="sub_tab__demo").value == "Gamma"


def test_sub_tab_bar_keys_are_namespaced_so_two_bars_do_not_collide():
    def script():
        from dashboard import components

        components.sub_tab_bar("page_a", "Category", ["X", "Y"])
        components.sub_tab_bar("page_b", "Statement", ["P", "Q"])

    at = AppTest.from_function(script)
    at.run()
    assert at.exception == []
    keys = {r.key for r in at.radio}
    assert "sub_tab__page_a" in keys
    assert "sub_tab__page_b" in keys


# --- C4: the multi-period statement table ---


def _col(period_end: str) -> str:
    """The dataframe's column header is `fmt.format_date`'s output, not the
    raw ISO period_end string -- matches what `statement_table` actually
    builds its columns from."""
    return fmt_module.format_date(period_end)


def _table_fixture(with_growth: bool = True, with_fallback: bool = False):
    periods = [{"period_end": "2025-03-31"}, {"period_end": "2025-06-30"}]
    rows = [
        {
            "label": "Revenue",
            "canonical": "revenue",
            "cells": [
                {"period_end": "2025-03-31", "value": 100_000_000, "growth_pct": None, "is_derived_quarter": False},
                {
                    "period_end": "2025-06-30", "value": 150_000_000,
                    "growth_pct": 0.5 if with_growth else None, "is_derived_quarter": with_fallback,
                },
            ],
        },
    ]
    return rows, periods


def test_statement_table_renders_growth_as_a_separate_row_beneath_the_value():
    def script(rows, periods):
        from dashboard import components

        components.statement_table(rows, periods, show_growth=True, key="t")

    rows, periods = _table_fixture(with_growth=True)
    at = AppTest.from_function(script, kwargs={"rows": rows, "periods": periods})
    at.run()
    assert at.exception == []
    df = at.dataframe[0].value
    assert df.iloc[0][_col("2025-06-30")] == "150"  # the filed value itself, unmarked (R1: rendered in millions)
    # SPEC-008 C4 constraint 1: a separate row, directly beneath its line
    # item, carries the derived figure -- load-bearing, not decorative
    # (renamed from the old caption-based mechanism, same requirement).
    # SPEC-008-batch-3 item 7 (approved 2026-08-14): the label now states
    # the comparison direction explicitly (columns run newest-to-oldest).
    assert "Growth %" in df.iloc[1]["Line item"]
    assert df.iloc[1][_col("2025-06-30")] == "+50.0%"
    captions = [c.value for c in at.caption]
    assert any("derived by this project, not filed" in c for c in captions)


def test_statement_table_renders_n_slash_m_for_a_flagged_growth_cell():
    # SPEC-008-batch-1 item 2 (D14): data.py's growth_not_meaningful flag
    # renders as "n/m", not the raw (misleading) percentage, and not blank
    # (blank means "nothing to compare", a different case).
    def script(rows, periods):
        from dashboard import components

        rows[0]["cells"][1]["growth_not_meaningful"] = "near_zero_base"
        components.statement_table(rows, periods, show_growth=True, key="t")

    rows, periods = _table_fixture(with_growth=True)
    at = AppTest.from_function(script, kwargs={"rows": rows, "periods": periods})
    at.run()
    assert at.exception == []
    df = at.dataframe[0].value
    assert df.iloc[1][_col("2025-06-30")] == "n/m"


def test_statement_table_omits_growth_when_toggle_is_off():
    def script(rows, periods):
        from dashboard import components

        components.statement_table(rows, periods, show_growth=False, key="t")

    rows, periods = _table_fixture(with_growth=True)
    at = AppTest.from_function(script, kwargs={"rows": rows, "periods": periods})
    at.run()
    df = at.dataframe[0].value
    assert len(df) == 1  # no growth sub-row at all
    captions = [c.value for c in at.caption]
    assert not any("%" in c for c in captions)


def test_statement_table_marks_derived_quarter_cells_and_adds_a_footnote():
    def script(rows, periods):
        from dashboard import components

        components.statement_table(rows, periods, show_growth=True, key="t")

    rows, periods = _table_fixture(with_growth=False, with_fallback=True)
    at = AppTest.from_function(script, kwargs={"rows": rows, "periods": periods})
    at.run()
    df = at.dataframe[0].value
    assert "†" in df.iloc[0][_col("2025-06-30")]
    captions = [c.value for c in at.caption]
    assert any("derived" in c and "subtracts the prior quarter" in c for c in captions)


def test_statement_table_blank_cell_marks_with_default_gap_cause():
    def script(rows, periods):
        from dashboard import components

        rows[0]["cells"][1]["value"] = None
        components.statement_table(rows, periods, show_growth=True, key="t")

    rows, periods = _table_fixture()
    at = AppTest.from_function(script, kwargs={"rows": rows, "periods": periods})
    at.run()
    df = at.dataframe[0].value
    assert df.iloc[0][_col("2025-06-30")] == "—"  # cell has no blank_cause -- defaults to "gap", the bare marker
    captions = [c.value for c in at.caption]
    assert any(c.startswith("— not tagged") for c in captions)


def test_statement_table_split_blank_cause_gets_a_distinct_marker_and_footnote():
    def script(rows, periods):
        from dashboard import components

        rows[0]["cells"][0]["value"] = None
        rows[0]["cells"][0]["blank_cause"] = "split"
        rows[0]["cells"][0]["blank_reason"] = "this period's figure is in the other row instead"
        components.statement_table(rows, periods, show_growth=False, key="t")

    rows, periods = _table_fixture()
    at = AppTest.from_function(script, kwargs={"rows": rows, "periods": periods})
    at.run()
    assert at.exception == []
    df = at.dataframe[0].value
    assert df.iloc[0][_col("2025-03-31")] == "— °"
    captions = [c.value for c in at.caption]
    assert any(c.startswith("° ") for c in captions)
    assert not any(c.startswith("— not tagged") for c in captions)  # no plain "gap" cell in this fixture


def test_statement_table_shows_all_periods_with_no_windowing_or_checkbox():
    # Live review, C4: the "show full history" checkbox and its hidden
    # default window were removed -- every period renders, always, no
    # widget deciding otherwise.
    periods = [{"period_end": f"2020-{m:02d}-15"} for m in range(1, 13)]  # far more than the old default of 8
    rows = [
        {
            "label": "Revenue",
            "canonical": "revenue",
            "cells": [
                {"period_end": p["period_end"], "value": i * 1_000_000, "growth_pct": None, "is_derived_quarter": False}
                for i, p in enumerate(periods)
            ],
        },
    ]

    def script(rows, periods):
        from dashboard import components

        components.statement_table(rows, periods, show_growth=False, key="t")

    at = AppTest.from_function(script, kwargs={"rows": rows, "periods": periods})
    at.run()
    assert at.exception == []
    df = at.dataframe[0].value
    # SPEC-008-batch-3 item 7 (approved 2026-08-14): every period still
    # renders (this test's own original point), but newest-to-oldest,
    # left to right, not the caller's oldest-to-newest order -- the
    # fallback's own reversal.
    assert list(df.columns)[1:] == [_col(p["period_end"]) for p in reversed(periods)]
    assert not at.checkbox


def test_statement_table_empty_when_no_rows():
    def script():
        from dashboard import components

        components.statement_table([], [{"period_end": "2025-03-31"}], show_growth=False, key="t")

    at = AppTest.from_function(script)
    at.run()
    assert at.exception == []
    assert any(i.value for i in at.info)


def test_statement_table_formats_eps_and_share_rows_in_their_own_units():
    # SPEC-008-batch-1 render-batch follow-up item 1 (approved 2026-08-11):
    # eps_basic/eps_diluted and basic_shares/diluted_shares must NOT go
    # through fmt.format_usd's $-millions convention -- a $2.35 diluted EPS
    # rendered that way would show as "0".
    def script(rows, periods):
        from dashboard import components

        components.statement_table(rows, periods, show_growth=False, key="t")

    periods = [{"period_end": "2025-03-31"}]
    rows = [
        {
            "label": "Diluted EPS", "canonical": "eps_diluted",
            "cells": [{"period_end": "2025-03-31", "value": 2.35, "is_derived_quarter": False}],
        },
        {
            "label": "Diluted shares outstanding", "canonical": "diluted_shares",
            "cells": [{"period_end": "2025-03-31", "value": 10_874_000_000, "is_derived_quarter": False}],
        },
        {
            "label": "Revenue", "canonical": "revenue",
            "cells": [{"period_end": "2025-03-31", "value": 150_000_000, "is_derived_quarter": False}],
        },
    ]
    at = AppTest.from_function(script, kwargs={"rows": rows, "periods": periods})
    at.run()
    assert at.exception == []
    df = at.dataframe[0].value
    assert df.iloc[0][_col("2025-03-31")] == "$2.35"
    assert df.iloc[1][_col("2025-03-31")] == "10,874"
    assert df.iloc[2][_col("2025-03-31")] == "150"  # revenue: unaffected, still $m


def test_statement_table_formats_fcff_tax_rate_as_a_percent():
    # SPEC-008-batch-3 item 2 (approved 2026-08-14): fcff_tax_rate is a
    # fraction (0.118) -- falling through to fmt.format_usd rendered it
    # as a flat "0" in every column, for every company. AMZN FY2020's real
    # rate (recovered from the FCFF figures in the item's own text): 11.8%.
    def script(rows, periods):
        from dashboard import components

        components.statement_table(rows, periods, show_growth=False, key="t")

    periods = [{"period_end": "2020-12-31"}]
    rows = [
        {
            "label": "Effective tax rate (FCFF)", "canonical": "fcff_tax_rate",
            "cells": [{"period_end": "2020-12-31", "value": 0.118, "is_derived_quarter": False}],
        },
    ]
    at = AppTest.from_function(script, kwargs={"rows": rows, "periods": periods})
    at.run()
    assert at.exception == []
    df = at.dataframe[0].value
    assert df.iloc[0][_col("2020-12-31")] == "11.8%"


def test_parenthesize_if_negative_wraps_and_strips_the_minus_sign():
    # SPEC-008-batch-3 item 4 (approved 2026-08-14): standard financial-
    # statement convention. Keyed off the raw value, not by re-parsing the
    # formatted text -- fmt.format_usd_per_share puts its own minus AFTER
    # the "$" ("$-1.50"), and the parens must still land around the whole
    # thing with the stray minus gone, not "($-1.50)".
    assert components._parenthesize_if_negative("18,171", -18_171) == "(18,171)"
    assert components._parenthesize_if_negative("$-1.50", -1.5) == "($1.50)"


def test_parenthesize_if_negative_leaves_positive_values_and_marks_alone():
    assert components._parenthesize_if_negative("18,171", 18_171) == "18,171"
    assert components._parenthesize_if_negative("0", 0) == "0"


def test_parenthesize_if_negative_produces_a_real_negative_zero_as_paren_zero():
    # The item's own explicit requirement: "(0) for a real negative zero"
    # must stay distinguishable from a missing value's dash. Python's own
    # formatting preserves the sign on a value that rounds to zero at
    # display precision ("-0", not "0") -- this is what turns THAT into
    # "(0)", never silently "0".
    text = fmt_module.format_usd(-40_000)  # -40,000 / 1e6 rounds to "-0" at 0dp
    assert text == "-0"
    assert components._parenthesize_if_negative(text, -40_000) == "(0)"


def test_statement_table_renders_negative_values_in_parentheses_not_with_a_minus_sign():
    def script(rows, periods):
        from dashboard import components

        components.statement_table(rows, periods, show_growth=False, key="t")

    periods = [{"period_end": "2025-03-31"}, {"period_end": "2025-06-30"}, {"period_end": "2025-09-30"}]
    rows = [
        {
            "label": "Net change in cash", "canonical": "net_change_in_cash",
            "cells": [
                {"period_end": "2025-03-31", "value": -8_821_000_000, "is_derived_quarter": False},
                {"period_end": "2025-06-30", "value": 100_000_000, "is_derived_quarter": False},
                {"period_end": "2025-09-30", "value": None, "is_derived_quarter": False, "blank_cause": "gap"},
            ],
        },
    ]
    at = AppTest.from_function(script, kwargs={"rows": rows, "periods": periods})
    at.run()
    assert at.exception == []
    df = at.dataframe[0].value
    assert df.iloc[0][_col("2025-03-31")] == "(8,821)"  # not "-8,821"
    assert df.iloc[0][_col("2025-06-30")] == "100"  # positive: unaffected
    assert df.iloc[0][_col("2025-09-30")] == "—"  # blank stays the em-dash marker, distinguishable from "(0)"


def test_statement_table_period_columns_are_right_aligned_line_item_column_stays_left():
    # SPEC-008-batch-3 item 4 (approved 2026-08-14): TextColumn exposes
    # `alignment` natively in the installed 1.60.0 (confirmed against its
    # own signature before using it) -- no Styler CSS needed. AppTest's
    # dataframe element doesn't expose column_config as a Python object
    # directly, but the underlying protobuf's `columns` field carries it
    # as JSON, which is what this reads.
    import json

    def script(rows, periods):
        from dashboard import components

        components.statement_table(rows, periods, show_growth=False, key="t")

    rows, periods = _table_fixture(with_growth=False)
    at = AppTest.from_function(script, kwargs={"rows": rows, "periods": periods})
    at.run()
    assert at.exception == []
    column_config = json.loads(at.dataframe[0].proto.columns)
    assert column_config[components._LINE_ITEM_COL]["alignment"] == "left"
    assert column_config[_col("2025-06-30")]["alignment"] == "right"


def test_fiscal_column_label_annual_leads_with_fy_and_year():
    # SPEC-008-batch-3 item 5 (approved 2026-08-14). A genuine 365-day
    # annual duration -- period_start matters here (see the Q4-vs-annual
    # disambiguation test below), so it's included even though this case
    # doesn't exercise that branch.
    period = {
        "period_start": "2025-01-01", "period_end": "2025-12-31",
        "fiscal_year": 2025, "fiscal_period": "FY",
    }
    assert components._fiscal_column_label(period) == "FY2025"


def test_fiscal_column_label_quarterly_leads_with_quarter_and_two_digit_year():
    period = {
        "period_start": "2025-10-27", "period_end": "2026-01-25",
        "fiscal_year": 2026, "fiscal_period": "Q3",
    }
    assert components._fiscal_column_label(period) == "Q3 FY26"


def test_fiscal_column_label_disambiguates_fy_between_annual_total_and_fourth_quarter():
    # Found live against real NVDA data while checking this item: this
    # project's own fiscal-period vocabulary has no separate "Q4" value --
    # the filing that reports the fourth quarter IS the annual 10-K, so
    # `filings.fiscal_period` is "FY" for BOTH a genuinely annual-duration
    # period and a genuinely quarterly-duration one landing at fiscal year
    # end. Duration (from period_start/period_end) is what disambiguates
    # them -- the SAME fiscal_year/fiscal_period pair reads differently
    # depending on which table column it's in.
    annual = components._fiscal_column_label(
        {"period_start": "2025-01-27", "period_end": "2026-01-25", "fiscal_year": 2026, "fiscal_period": "FY"}
    )
    quarterly = components._fiscal_column_label(
        {"period_start": "2025-10-27", "period_end": "2026-01-25", "fiscal_year": 2026, "fiscal_period": "FY"}
    )
    assert annual == "FY2026"
    assert quarterly == "Q4 FY26"


def test_fiscal_column_label_micron_consecutive_fiscal_years_read_as_such():
    # The item's own worked example: Micron's floating fiscal year-end
    # (late August one year, early September the next) makes "Sep 3, 2020"
    # and "Aug 29, 2019" look like an inconsistent date series with
    # nothing on screen saying they're consecutive fiscal years.
    fy2019 = components._fiscal_column_label(
        {"period_start": "2018-08-30", "period_end": "2019-08-29", "fiscal_year": 2019, "fiscal_period": "FY"}
    )
    fy2020 = components._fiscal_column_label(
        {"period_start": "2019-08-30", "period_end": "2020-09-03", "fiscal_year": 2020, "fiscal_period": "FY"}
    )
    assert (fy2019, fy2020) == ("FY2019", "FY2020")


def test_fiscal_column_label_fails_closed_to_the_calendar_date_when_no_fiscal_label_on_record():
    # data.get_statement_periods sets fiscal_year/fiscal_period to None
    # when no filing on record carries a label for this period -- never a
    # guessed year, the calendar date alone instead.
    period = {"period_end": "2025-12-31", "fiscal_year": None, "fiscal_period": None}
    assert components._fiscal_column_label(period) == fmt_module.format_date("2025-12-31")


def test_statement_table_column_headers_lead_with_the_fiscal_label_date_moves_to_the_tooltip():
    import json

    def script(rows, periods):
        from dashboard import components

        components.statement_table(rows, periods, show_growth=False, key="t")

    periods = [{"period_end": "2026-01-25", "fiscal_year": 2026, "fiscal_period": "Q3"}]
    rows = [
        {
            "label": "Revenue", "canonical": "revenue",
            "cells": [{"period_end": "2026-01-25", "value": 100_000_000, "is_derived_quarter": False}],
        },
    ]
    at = AppTest.from_function(script, kwargs={"rows": rows, "periods": periods})
    at.run()
    assert at.exception == []
    column_config = json.loads(at.dataframe[0].proto.columns)
    col_key = _col("2026-01-25")  # the DataFrame's own column key is unchanged, still the date
    assert column_config[col_key]["label"] == "Q3 FY26"
    assert column_config[col_key]["help"] == col_key  # the calendar date, secondary, in the tooltip


def test_statement_table_widens_the_line_item_column_past_the_longest_real_label():
    # SPEC-008-batch-3 item 6 (approved 2026-08-14): no explicit width
    # meant "sized to fit the cell contents" per column_config's own docs,
    # but the grid still shows a single-line, ellipsis-truncated cell past
    # whatever width that produced -- this is what "Property, plant and
    # equipment and…" was. An explicit, generously-sized width fixes it;
    # confirms the actual column_config value, not just that SOME width
    # was set.
    import json

    def script(rows, periods):
        from dashboard import components

        components.statement_table(rows, periods, show_growth=False, key="t")

    rows, periods = _table_fixture(with_growth=False)
    at = AppTest.from_function(script, kwargs={"rows": rows, "periods": periods})
    at.run()
    assert at.exception == []
    column_config = json.loads(at.dataframe[0].proto.columns)
    assert column_config[components._LINE_ITEM_COL]["width"] == components._LINE_ITEM_COL_WIDTH_PX
    # Wide enough for the real registry's own longest label, not just an
    # arbitrary bigger number -- proves the width was actually SIZED to
    # something, not picked blind. SPEC-008-batch-4 follow-up item 1
    # (approved 2026-08-18) shortened the labels further ("PP&E and
    # finance-lease ROU assets, net", 39 characters, is now "PP&E &
    # finance-lease ROU assets, net", 36) -- this is the new real worst
    # case, not the old one.
    longest_real_label = "PP&E & finance-lease ROU assets, net"
    assert components._LINE_ITEM_COL_WIDTH_PX > len(longest_real_label) * 7  # rough px-per-character floor


def test_statement_table_period_columns_are_wider_than_the_old_75px_default():
    import json

    def script(rows, periods):
        from dashboard import components

        components.statement_table(rows, periods, show_growth=False, key="t")

    rows, periods = _table_fixture(with_growth=False)
    at = AppTest.from_function(script, kwargs={"rows": rows, "periods": periods})
    at.run()
    assert at.exception == []
    column_config = json.loads(at.dataframe[0].proto.columns)
    assert column_config[_col("2025-06-30")]["width"] == components._PERIOD_COL_WIDTH_PX
    assert components._PERIOD_COL_WIDTH_PX > 75


# --- SPEC-008-batch-3 item 7 (approved 2026-08-14): reversed-column fallback ---
# Routes 1 (CSS direction:rtl via an injected <style> block) and 2 (a native
# scroll-position field) were both checked against the installed 1.60.0 and
# neither produced a confirmable fix -- see SPEC-008-batch-3.md's own
# resolution for the investigation. Columns now run newest-to-oldest, left
# to right, so the table opens already showing the newest period without
# scrolling; growth keeps comparing each period to the chronologically
# PRIOR one, which now sits to a cell's right.


def _three_period_fixture():
    """Q1=100M, Q2=150M (+50% vs Q1), Q3=90M (-40% vs Q2) -- growth_pct is
    ALREADY computed here, in chronological cell order, exactly as
    data.py would hand it to the render layer; statement_table must
    display these values unchanged, never recompute them from whichever
    columns end up visually adjacent after reversal."""
    periods = [
        {"period_end": "2025-03-31"}, {"period_end": "2025-06-30"}, {"period_end": "2025-09-30"},
    ]
    rows = [
        {
            "label": "Revenue", "canonical": "revenue",
            "cells": [
                {"period_end": "2025-03-31", "value": 100_000_000, "growth_pct": None, "is_derived_quarter": False},
                {"period_end": "2025-06-30", "value": 150_000_000, "growth_pct": 0.5, "is_derived_quarter": False},
                {"period_end": "2025-09-30", "value": 90_000_000, "growth_pct": -0.4, "is_derived_quarter": False},
            ],
        },
    ]
    return rows, periods


def test_statement_table_columns_run_newest_to_oldest_left_to_right():
    def script(rows, periods):
        from dashboard import components

        components.statement_table(rows, periods, show_growth=False, key="t")

    rows, periods = _three_period_fixture()
    at = AppTest.from_function(script, kwargs={"rows": rows, "periods": periods})
    at.run()
    assert at.exception == []
    df = at.dataframe[0].value
    assert list(df.columns)[1:] == [_col("2025-09-30"), _col("2025-06-30"), _col("2025-03-31")]


def test_statement_table_reversed_columns_keep_growth_comparing_to_the_chronologically_prior_period():
    # The item's own explicit ask: "Add a test that a known growth figure
    # is unchanged by the reversal; this is exactly the kind of flip that
    # silently inverts a sign." Q2's growth (+50%, vs Q1) and Q3's growth
    # (-40%, vs Q2) must render EXACTLY as computed, under their own
    # columns, regardless of the columns' new left-to-right positions.
    def script(rows, periods):
        from dashboard import components

        components.statement_table(rows, periods, show_growth=True, key="t")

    rows, periods = _three_period_fixture()
    at = AppTest.from_function(script, kwargs={"rows": rows, "periods": periods})
    at.run()
    assert at.exception == []
    df = at.dataframe[0].value
    growth_row = df.iloc[1]
    assert growth_row[_col("2025-03-31")] == ""  # no prior period to compare against
    assert growth_row[_col("2025-06-30")] == "+50.0%"  # NOT -50.0% or any inverted variant
    assert growth_row[_col("2025-09-30")] == "-40.0%"


def test_statement_table_growth_row_label_states_the_comparison_direction():
    # SPEC-008-batch-4 item 1 (approved 2026-08-16) moved this OUT of the
    # per-row label ("↳ Growth % (vs. period to the right)" repeated on
    # every second row and was a major width contributor) and into the
    # table's own caption, stated once -- still unambiguous, just not
    # repeated. The row label itself is now the short "↳ Growth %".
    def script(rows, periods):
        from dashboard import components

        components.statement_table(rows, periods, show_growth=True, key="t")

    rows, periods = _three_period_fixture()
    at = AppTest.from_function(script, kwargs={"rows": rows, "periods": periods})
    at.run()
    assert at.exception == []
    df = at.dataframe[0].value
    assert df.iloc[1]["Line item"].strip() == "↳ Growth %"
    captions = [c.value for c in at.caption]
    assert any("right" in c.lower() for c in captions)


def test_statement_table_caption_states_the_column_order():
    # Found while fixing this item, not asked for: item 7's own column
    # reversal shipped with no on-screen notice of the new order at all.
    # Stated unconditionally (not gated on show_growth), since it governs
    # reading the whole table, not just the growth row.
    def script(rows, periods):
        from dashboard import components

        components.statement_table(rows, periods, show_growth=False, key="t")

    rows, periods = _table_fixture(with_growth=False)
    at = AppTest.from_function(script, kwargs={"rows": rows, "periods": periods})
    at.run()
    assert at.exception == []
    captions = [c.value for c in at.caption]
    assert any("newest to oldest" in c.lower() for c in captions)


def test_statement_table_style_bolds_subtotal_rows():
    # SPEC-008-batch-3 item 1 (approved 2026-08-13): font-weight is the
    # confirmed-safe Styler property (unlike padding, used for the
    # indentation half of this item instead -- see the text-prefix test
    # below). Direct unit test of the style function itself, no need to
    # inspect Streamlit's rendered Styler HTML.
    row = pd.Series(["x", "y"], name=0)
    style = components._statement_table_style(row, row_kind=["subtotal_value"], row_group=[0])
    assert all("font-weight: bold" in s for s in style)
    plain_style = components._statement_table_style(row, row_kind=["value"], row_group=[0])
    assert all("font-weight: bold" not in s for s in plain_style)


def test_statement_table_indents_cash_flow_subtotal_labels_not_other_rows():
    # Item 1: cfo/net_cash_investing/net_cash_financing/cash_and_restricted_
    # cash get a text indent (Streamlit's Styler never confirmed to honour
    # CSS padding -- _GROWTH_ROW_LABEL's own turned-arrow prefix already
    # established text-indentation as this table's working pattern).
    # net_change_in_cash and cash_beginning are the item's own explicit
    # exclusions -- must NOT be indented despite being on the same
    # statement, right next to the rows that are.
    def script(rows, periods):
        from dashboard import components

        components.statement_table(rows, periods, show_growth=False, key="t")

    periods = [{"period_end": "2025-03-31"}]

    def _row(canonical, label):
        return {
            "label": label, "canonical": canonical,
            "cells": [{"period_end": "2025-03-31", "value": 10_000_000, "is_derived_quarter": False}],
        }

    rows = [
        _row("cfo", "Net cash provided by operating activities"),
        _row("net_cash_investing", "Net cash used in investing activities"),
        _row("net_cash_financing", "Net cash provided by (used in) financing activities"),
        _row("cash_and_restricted_cash", "Cash at end of period"),
        _row("net_change_in_cash", "Net change in cash"),
        _row("cash_beginning", "Cash at beginning of period"),
    ]
    at = AppTest.from_function(script, kwargs={"rows": rows, "periods": periods})
    at.run()
    assert at.exception == []
    df = at.dataframe[0].value
    labels = df[components._LINE_ITEM_COL].tolist()
    assert labels[0] == components._SUBTOTAL_INDENT + "Net cash provided by operating activities"
    assert labels[1] == components._SUBTOTAL_INDENT + "Net cash used in investing activities"
    assert labels[2] == components._SUBTOTAL_INDENT + "Net cash provided by (used in) financing activities"
    assert labels[3] == components._SUBTOTAL_INDENT + "Cash at end of period"
    assert labels[4] == "Net change in cash"  # unindented, the item's own instruction
    assert labels[5] == "Cash at beginning of period"  # unindented, the item's own instruction


def test_statement_table_subtotal_treatment_never_depends_on_the_cells_own_value():
    # "Weight and indentation must be a function of the row's structural
    # role, never of its data" -- the item's own words. A subtotal row
    # stays indented and bold whether its value is positive, negative, or
    # blank; a non-subtotal row never gets either regardless of its value.
    def script(rows, periods):
        from dashboard import components

        components.statement_table(rows, periods, show_growth=False, key="t")

    periods = [{"period_end": "2025-03-31"}, {"period_end": "2025-06-30"}]
    rows = [
        {
            "label": "Net cash provided by operating activities", "canonical": "cfo",
            "cells": [
                {"period_end": "2025-03-31", "value": -5_000_000, "is_derived_quarter": False},
                {"period_end": "2025-06-30", "value": None, "is_derived_quarter": False, "blank_cause": "gap"},
            ],
        },
    ]
    at = AppTest.from_function(script, kwargs={"rows": rows, "periods": periods})
    at.run()
    assert at.exception == []
    df = at.dataframe[0].value
    assert df.iloc[0][components._LINE_ITEM_COL] == (
        components._SUBTOTAL_INDENT + "Net cash provided by operating activities"
    )


# --- SPEC-008-batch-4 follow-up item 3 (approved 2026-08-19): model-ready CSV export ---
#
# `build_raw_export_rows` is tested directly, as a pure function, rather
# than through AppTest -- `st.download_button`'s file bytes aren't reachable
# through AppTest (`DownloadButtonProto` exposes a `deferred_file_id`, no
# `data` field), so a real test of the actual VALUES needs a function
# callable outside of Streamlit entirely. The button itself (label,
# file_name, no-exception) is still checked once through AppTest below.


def test_raw_export_units_cover_exactly_the_cell_formatter_exceptions():
    # Reused, not re-decided: the raw export's own scale/unit tables must
    # name exactly the same canonicals _CELL_FORMATTERS already treats as
    # exceptions to the default $m-usd rule -- if the two ever drifted,
    # the on-screen table and this export could disagree about what a row
    # even IS (dollars vs. millions vs. a fraction) without either being
    # obviously wrong on its own.
    assert set(components._RAW_EXPORT_SCALE) == set(components._CELL_FORMATTERS)
    assert set(components._RAW_EXPORT_UNIT_SUFFIX) == set(components._CELL_FORMATTERS)


def test_raw_export_scales_usd_to_millions_like_the_on_screen_table():
    rows = [{"canonical": "revenue", "label": "Revenue", "cells": [{"value": 637_960_000_000}]}]
    raw = components.build_raw_export_rows(rows, ["Jun 30, 2026"], show_growth=False)
    assert raw[0]["Jun 30, 2026"] == 637_960.0
    assert raw[0][components._LINE_ITEM_COL] == "Revenue ($m)"


def test_raw_export_keeps_negatives_as_a_plain_minus_never_parentheses():
    rows = [{"canonical": "free_cash_flow", "label": "Free cash flow", "cells": [{"value": -18_171_000_000}]}]
    raw = components.build_raw_export_rows(rows, ["Jun 30, 2026"], show_growth=False)
    assert raw[0]["Jun 30, 2026"] == -18_171.0
    csv_text = pd.DataFrame(raw, columns=[components._LINE_ITEM_COL, "Jun 30, 2026"]).to_csv(index=False)
    assert "(18,171)" not in csv_text
    assert "-18171" in csv_text or "-18171.0" in csv_text


def test_raw_export_does_not_scale_eps_or_the_tax_rate_fraction():
    rows = [
        {"canonical": "eps_diluted", "label": "Diluted EPS", "cells": [{"value": 2.3541}]},
        {"canonical": "fcff_tax_rate", "label": "Effective tax rate (FCFF)", "cells": [{"value": 0.118}]},
    ]
    raw = components.build_raw_export_rows(rows, ["Jun 30, 2026"], show_growth=False)
    assert raw[0]["Jun 30, 2026"] == 2.3541  # dollars, not millions
    assert raw[0][components._LINE_ITEM_COL] == "Diluted EPS ($)"
    assert raw[1]["Jun 30, 2026"] == 0.118  # already a fraction -- not *100, never a '%' suffix
    assert raw[1][components._LINE_ITEM_COL] == "Effective tax rate (FCFF) (decimal fraction)"


def test_raw_export_scales_share_counts_to_millions():
    rows = [{"canonical": "diluted_shares", "label": "Diluted shares outstanding", "cells": [{"value": 10_743_000_000}]}]
    raw = components.build_raw_export_rows(rows, ["Jun 30, 2026"], show_growth=False)
    assert raw[0]["Jun 30, 2026"] == 10_743.0
    assert raw[0][components._LINE_ITEM_COL] == "Diluted shares outstanding (shares, m)"


def test_raw_export_blank_cells_are_none_never_an_em_dash_or_n_m():
    rows = [{"canonical": "revenue", "label": "Revenue", "cells": [{"value": None, "blank_cause": "gap"}]}]
    raw = components.build_raw_export_rows(rows, ["Jun 30, 2026"], show_growth=False)
    assert raw[0]["Jun 30, 2026"] is None
    csv_text = pd.DataFrame(raw, columns=[components._LINE_ITEM_COL, "Jun 30, 2026"]).to_csv(index=False)
    assert "—" not in csv_text  # em dash
    assert "n/m" not in csv_text


def test_raw_export_growth_rows_are_decimal_fractions_not_percent_strings():
    rows = [
        {
            "canonical": "revenue", "label": "Revenue",
            "cells": [{"value": 100_000_000, "growth_pct": 0.101, "growth_not_meaningful": None}],
        },
    ]
    raw = components.build_raw_export_rows(rows, ["Jun 30, 2026"], show_growth=True)
    assert len(raw) == 2
    growth_row = raw[1]
    assert growth_row[components._LINE_ITEM_COL] == "↳ Growth % (decimal fraction)"
    assert growth_row["Jun 30, 2026"] == 0.101  # not "+10.1%"


def test_raw_export_growth_not_meaningful_is_blank_not_the_n_m_marker():
    rows = [
        {
            "canonical": "revenue", "label": "Revenue",
            "cells": [{"value": 100_000_000, "growth_pct": None, "growth_not_meaningful": True}],
        },
    ]
    raw = components.build_raw_export_rows(rows, ["Jun 30, 2026"], show_growth=True)
    assert raw[1]["Jun 30, 2026"] is None


def test_raw_export_omits_growth_rows_when_toggle_is_off():
    rows = [
        {
            "canonical": "revenue", "label": "Revenue",
            "cells": [{"value": 100_000_000, "growth_pct": 0.1, "growth_not_meaningful": None}],
        },
    ]
    raw = components.build_raw_export_rows(rows, ["Jun 30, 2026"], show_growth=False)
    assert len(raw) == 1  # value row only -- same row set the on-screen table shows with growth off


def test_raw_export_row_and_column_structure_matches_the_display_table():
    # Same number of rows (value + growth), same period columns, same
    # ORDER -- the item's own "maps to what's on screen" requirement.
    rows, periods = _table_fixture(with_growth=True)
    period_cols = [fmt_module.format_date(p["period_end"]) for p in periods]
    raw = components.build_raw_export_rows(rows, period_cols, show_growth=True)
    assert len(raw) == 2 * len(rows)  # one value row + one growth row per line item
    assert list(raw[0].keys()) == [components._LINE_ITEM_COL] + period_cols


def test_statement_table_offers_a_raw_csv_download_button():
    def script(rows, periods):
        from dashboard import components

        components.statement_table(rows, periods, show_growth=True, key="AMZN_Income statement")

    rows, periods = _table_fixture(with_growth=True)
    at = AppTest.from_function(script, kwargs={"rows": rows, "periods": periods})
    at.run()
    assert at.exception == []
    buttons = at.get("download_button")
    assert len(buttons) == 1
    assert "raw" in buttons[0].proto.label.lower()
    assert buttons[0].proto.id  # a real widget, not a no-op
