"""SPEC-008 R5: Metrics page. Composition only.

With one company selected this is the individual view; with two or three it
is the comparison -- same page, same charts, no mode switch (R5, R7: no
separate Compare page)."""

from __future__ import annotations

import io

import streamlit as st

from dashboard import components, data, format as fmt
from edgar import config


def _group_metrics() -> dict[str, list[str]]:
    """Category -> metric names, in the order each category FIRST appears
    while walking `METRIC_REGISTRY` -- never a hardcoded category list
    (SPEC-008 C6/AC5: a metric added under a new category must appear with
    no page-file changes; a hardcoded list of category names would
    silently drop it instead of failing loudly). `dict.setdefault` here
    means `groups`' own key order already IS registry order -- nothing
    else needs to track or re-derive it."""
    groups: dict[str, list[str]] = {}
    for name, mdef in config.METRIC_REGISTRY.items():
        groups.setdefault(mdef.group, []).append(name)
    return groups


def _render_metric(name: str, tickers: list[str], cik_by_ticker: dict[str, str], basis_choice: str, scale_choice: str) -> None:
    metric_def = config.METRIC_REGISTRY[name]
    basis = metric_def.basis if metric_def.basis != "both" else basis_choice
    if metric_def.basis != "both" and basis_choice != metric_def.basis and metric_def.basis in ("annual", "quarterly"):
        basis = metric_def.basis  # annual-/quarterly-only metrics ignore the toggle

    series_by_ticker = {t: data.get_metric_series(cik_by_ticker[t], name, basis) for t in tickers}
    scale = scale_choice if metric_def.unit == "usd" else "absolute"
    components.metric_chart(metric_def, series_by_ticker, cik_by_ticker, key_prefix=f"metrics_{name}", scale=scale)

    if st.toggle("Table view", key=f"table_{name}"):
        for t in tickers:
            st.write(f"**{t}**")
            rows = series_by_ticker[t]
            if not rows:
                components.empty_state(f"No data for {metric_def.display_name} ({t}).")
                continue
            for row in rows:
                # usd_per_share values carry a literal '$' (format_usd_per_share
                # is not itself escaped -- see its docstring); this is the one
                # place outside components.py's safe wrappers where a
                # formatted value reaches st.write, so it is escaped here.
                value_str = fmt.format_metric_value(row["value"], metric_def, row["null_reason"])
                st.write(fmt.escape_markdown_currency(f"{fmt.format_period_label(row['period_end'], basis)}: {value_str}"))
            csv_lines = ["period_end,value"] + [f"{r['period_end']},{r['value'] if r['value'] is not None else ''}" for r in rows]
            st.download_button(
                f"Download {metric_def.display_name} ({t}) CSV",
                data="\n".join(csv_lines),
                file_name=f"{t}_{name}_{basis}.csv",
                key=f"csv_{name}_{t}",
            )


def render() -> None:
    st.title("Metrics")
    selected_tickers = components.get_selected_tickers()
    companies = {c["ticker"]: c["cik"] for c in data.get_companies()}
    cik_by_ticker = {t: companies[t] for t in selected_tickers}

    basis_choice = st.radio("Period", options=["annual", "quarterly"], horizontal=True, key="metrics_basis")
    scale_choice = st.radio(
        "Scale (USD metrics)", options=["absolute", "indexed"], horizontal=True, key="metrics_scale",
        help="Absolute is honest about size; indexed (to 100 at the first period) makes trajectories comparable.",
    )

    # C6: sub-tabs by category, derived from METRIC_REGISTRY itself (never
    # a hardcoded category list -- see _group_metrics) -- both the set of
    # tabs and their labels come from the registry, not retyped here.
    groups = _group_metrics()
    category = components.sub_tab_bar("metrics_category", "Category", list(groups.keys()))

    st.header(category)
    for name in groups[category]:
        _render_metric(name, selected_tickers, cik_by_ticker, basis_choice, scale_choice)
