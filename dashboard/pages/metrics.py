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
                value_str = fmt.format_metric_value(row["value"], metric_def, row["null_reason"])
                st.write(f"{fmt.format_period_label(row['period_end'], basis)}: {value_str}")
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

    groups = _group_metrics()
    group_order = ["Growth", "Margins", "Returns", "Capital & Cash", "Working Capital", "Solvency", "Quality"]
    for group in group_order:
        names = groups.get(group, [])
        if not names:
            continue
        st.header(group)
        for name in names:
            _render_metric(name, selected_tickers, cik_by_ticker, basis_choice, scale_choice)
