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

SPEC-009 Part B (approved 2026-08-25): the page modules live in
`dashboard/app_pages/`, not `dashboard/pages/` -- deliberately NOT the
name Streamlit's own docs use for MPA-style auto-discovery, because the
directory name alone, not whether this file calls `st.navigation`, is
what triggers it. Read directly from the installed Streamlit source
(1.60.0) before deciding, not assumed from docs:
`PagesManager.uses_pages_directory` (`streamlit/runtime/pages_manager.py`)
is set to `True` purely by `Path(main_script_parent / "pages").exists()`
-- a class-level flag computed from the filesystem, independent of this
script's own code. When set, `script_runner.py` runs `_mpa_v1(...)`
INSTEAD OF this file's own top-level code: it globs every `.py` file in
that `pages/` directory, builds a page for each from its bare filename,
and calls its OWN internal `_navigation(...)` -- rendering that sidebar
BEFORE this script's own body (the auth gate, then the real
`st.navigation()` call below) ever runs. That is the exact leak this
project's own login screen showed: raw filenames (`app`, `filings`,
`financials`, `metrics`, `overview`) in the sidebar, on the pre-auth
screen specifically, because `st.stop()` fires inside `_mpa_v1`'s own
`page.run()` before this file's OWN navigation call is ever reached.
Confirmed not a bypass (the real page routing below is unaffected once
authenticated) -- but a public URL's LOGIN screen should show nothing at
all about internal structure, not even correct structure. Renaming this
directory means `uses_pages_directory` is never True in the first place,
so `_mpa_v1` never runs, and NOTHING is rendered pre-auth beyond the
password prompt itself.
"""

from __future__ import annotations

import streamlit as st

from dashboard import components
from dashboard.app_pages import filings, financials, metrics, overview

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

# SPEC-009 Part B: "the deployed app must show what filing it's current as
# of, on every page" -- rendered once, here, so every page gets it the same
# way `environment_caption()` above already does, rather than each page
# implementing (and possibly forgetting, or wording differently) its own.
components.data_freshness_caption()

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
