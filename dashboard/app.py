"""SPEC-008: entry point, auth, navigation.

Built on `st.navigation`/`st.Page` (v1.1), not the auto-discovered
`pages/`-directory convention. Under auto-discovery, this script's own body
would run only when it is itself the active page -- code here (the sidebar
selector, the auth gate) would not appear on the other three pages unless
each of them separately re-invoked it, with nothing structural stopping a
future page from forgetting to. `st.navigation`/`st.Page` genuinely
re-executes this script on every navigation, so the sidebar and the auth
gate are instantiated exactly once, here, before dispatching to whichever
page was selected -- every page gets them by construction. Prefer the
structure where the mistake is impossible over the one where it is merely
discouraged (ARCHITECTURE.md, SPEC-008 v1.1).
"""

from __future__ import annotations

import streamlit as st

from dashboard import components
from dashboard.pages import filings, financials, metrics, overview

st.set_page_config(page_title="Equity Research Dashboard", layout="wide")

# R2: password gate, checked before any page renders.
if not components.require_auth():
    st.stop()

# R4a: one control, everywhere -- rendered exactly once, here.
components.sidebar_company_selector()

# Found live (C4 rebuild): a bare `streamlit` on PATH silently resolved to a
# different install than this project's `.venv` -- always visible so that
# stops being a guess.
components.environment_caption()

pages = st.navigation(
    [
        # Every page module exposes a function named `render` (consistent,
        # simple convention) -- Streamlit infers a page's URL pathname from
        # the callable's name when url_path isn't given, so leaving these
        # implicit would collide all four onto the same "render" pathname
        # (found live, running the app for the first time: `st.navigation`
        # raised "Multiple Pages specified with URL pathname render").
        # Explicit url_path for each sidesteps it without renaming the
        # per-module convention.
        st.Page(overview.render, title="Overview", url_path="overview", default=True),
        st.Page(financials.render, title="Financials", url_path="financials"),
        st.Page(metrics.render, title="Metrics", url_path="metrics"),
        st.Page(filings.render, title="Filings", url_path="filings"),
    ]
)
pages.run()
