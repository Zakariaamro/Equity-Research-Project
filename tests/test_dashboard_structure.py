"""SPEC-008: architectural boundaries enforced by grep, not just by review.
'Pages contain no SQL' and 'components.py owns every st.session_state key'
are both rules a normal test can't catch (nothing breaks at runtime if a
page sneaks in a query or touches session_state directly) -- so they are
checked here, directly, against the source text."""

from __future__ import annotations

import ast
from pathlib import Path

DASHBOARD_DIR = Path(__file__).parent.parent / "dashboard"
PAGES_DIR = DASHBOARD_DIR / "pages"

_SQL_MARKERS = ("SELECT ", ".execute(", "import sqlite3", "from sqlite3")

# SPEC-008 review D1: these three functions render model-/DB-generated
# prose (brief sentences, finding quotes, observation statements) -- exactly
# the text that can contain an arbitrary '$', which Streamlit's markdown
# renders as LaTeX math-mode delimiters. They must render ONLY through
# components.py's `_safe_*` wrappers (which always escape), never call
# st.markdown/st.write/st.caption directly -- so a future edit can't forget
# the escape by omission, matching app.py's own "impossible, not merely
# discouraged" argument for its url_path= choice.
_NARRATIVE_RENDER_FUNCTIONS = ("brief_sentence", "observation_item", "finding_item")
_RAW_RENDER_CALLS = ("st.markdown(", "st.write(", "st.caption(")


def _function_source(path: Path, func_name: str) -> str:
    text = path.read_text()
    tree = ast.parse(text)
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == func_name:
            return ast.get_source_segment(text, node)
    raise AssertionError(f"{func_name!r} not found in {path}")


def _page_files() -> list[Path]:
    return sorted(p for p in PAGES_DIR.glob("*.py") if p.name != "__init__.py")


def test_no_sql_outside_data_module():
    """Pages contain no SQL. They call data.py."""
    offenders = []
    for path in _page_files() + [DASHBOARD_DIR / "components.py"]:
        text = path.read_text()
        for marker in _SQL_MARKERS:
            if marker in text:
                offenders.append(f"{path.name}: contains {marker!r}")
    assert not offenders, "\n".join(offenders)


def test_no_session_state_outside_components():
    """components.py owns every st.session_state key (SPEC-008 v1.1) --
    pages, and app.py, never touch it directly."""
    offenders = []
    for path in _page_files() + [DASHBOARD_DIR / "app.py"]:
        text = path.read_text()
        if "st.session_state" in text:
            offenders.append(path.name)
    assert not offenders, f"st.session_state referenced outside components.py in: {offenders}"


def test_components_module_is_the_only_one_using_session_state():
    """Sanity check on the test above: components.py DOES use it (otherwise
    the previous test would be vacuous)."""
    text = (DASHBOARD_DIR / "components.py").read_text()
    assert "st.session_state" in text


def test_narrative_renderers_use_the_escaping_wrapper():
    """SPEC-008 review, post-D1: brief_sentence/observation_item/finding_item
    must call only `_safe_markdown`/`_safe_write`/`_safe_caption`, never the
    raw st.* functions directly -- otherwise a future edit could silently
    drop the currency-escaping call and neither a unit test on the escaping
    helper nor AppTest (which asserts on values, not rendering) would catch
    it. This test would fail the moment a raw call reappears.

    Deliberately kept alongside test_no_unescaped_currency_reaches_
    streamlit_markdown below, not replaced by it: these three functions
    render ARBITRARY, DB-generated prose (`sentence["text"]` and similar --
    a Subscript, not a string literal or a resolvable module constant), so
    the general check below has nothing to statically resolve there and
    would pass silently even if the wrapper were bypassed. This one
    forbids the raw call outright, by name, regardless of content;
    the general check below covers everything the by-name list can't
    (every other STATIC string in the file, and every other file)."""
    path = DASHBOARD_DIR / "components.py"
    offenders = []
    for func_name in _NARRATIVE_RENDER_FUNCTIONS:
        source = _function_source(path, func_name)
        for marker in _RAW_RENDER_CALLS:
            if marker in source:
                offenders.append(f"{func_name}: calls {marker!r} directly instead of a _safe_* wrapper")
    assert not offenders, "\n".join(offenders)


# SPEC-008-batch-4 follow-up item 4 (approved 2026-08-20): the currency/
# LaTeX bug (Streamlit's markdown renderer treats a `$...$` span as inline
# LaTeX math mode, D1) recurred a THIRD time -- in a `download_button`'s
# `help=` tooltip, which the previous two guards never looked at, because
# both were built the same way: enumerate the Streamlit call that needs
# watching (`st.write` alone for D1; `st.markdown`/`st.write`/`st.caption`
# for batch 3 item 3). Streamlit also markdown-renders `help=` text on
# most widgets, widget labels, tab/expander labels, `st.info`/`warning`/
# `error`, and more -- enumerating THOSE by name would be the identical
# mistake, one layer larger, and permanently one call site behind
# whichever Streamlit feature gets used next.
#
# Inverted here, per that finding: this guard does not know or care what a
# "render call" is, and names no Streamlit function anywhere below. It
# finds every string literal (including an f-string's own static pieces)
# anywhere under dashboard/ containing a bare, unescaped '$' -- excluding
# docstrings, which are documentation, never a runtime value Streamlit
# could render -- and requires each one to be accounted for: either
# pre-escaped in source (`\$`, not `$`) or named on the allowlist below
# with a written reason. A future Streamlit call that renders markdown
# needs NO change here at all -- whatever string it's handed was already
# caught (or already allowlisted) the moment it was written, regardless of
# which call eventually consumes it.


def _docstring_constant_ids(tree: ast.Module) -> set[int]:
    """id() of every Constant node that is a module/function/class
    docstring -- documentation, never a value passed to Streamlit at
    runtime, so out of scope for this guard by construction. This is a
    structural fact about what a docstring IS, not an enumerated 'safe
    sink' of the kind this guard exists to stop relying on."""
    ids: set[int] = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            body = node.body
            if (
                body and isinstance(body[0], ast.Expr)
                and isinstance(body[0].value, ast.Constant) and isinstance(body[0].value.value, str)
            ):
                ids.add(id(body[0].value))
    return ids


def _has_unescaped_dollar(s: str) -> bool:
    """A `\\$` is already escaped (SPEC-008 review D1's own fix); a bare
    `$` is not."""
    return any(ch == "$" and (i == 0 or s[i - 1] != "\\") for i, ch in enumerate(s))


def _dollar_string_literals(path: Path) -> list[tuple[int, str]]:
    """Every non-docstring string literal in `path` containing a bare '$'
    -- (line, text) pairs. Walking `ast.walk` reaches an f-string's own
    static text pieces too (a `JoinedStr`'s literal segments are their own
    `Constant` nodes), so `f"**{title}** ($m)"` is caught the same as a
    plain literal, with no special-case needed for f-strings at all."""
    text = path.read_text()
    tree = ast.parse(text)
    doc_ids = _docstring_constant_ids(tree)
    found: list[tuple[int, str]] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Constant) and isinstance(node.value, str) and id(node) not in doc_ids:
            if _has_unescaped_dollar(node.value):
                found.append((node.lineno, node.value))
    return found


# Every legitimate '$'-bearing string currently in dashboard/, named
# explicitly with why it's safe. Keyed by (filename, line, exact string)
# -- not just filename+string -- so two unrelated bare "$" literals in the
# same file (there are two, in format.py, for different reasons) can't
# collide under one entry and silently allow a future THIRD one for a
# reason nobody checked. Any edit to a listed string's own content, or a
# line it moves to, drops it out of this dict and back into the failing
# set -- deliberately: the point of an allowlist is that every entry was
# looked at, not that it was looked at once, in the past, forever.
_ALLOWED_DOLLAR_STRINGS: dict[tuple[str, int, str], str] = {
    ("components.py", 328, " ($)"): (
        "SPEC-008-batch-4 follow-up item 3: _RAW_EXPORT_UNIT_SUFFIX['eps_basic'], appended to "
        "the raw CSV export's own Line item labels (pandas .to_csv) -- never reaches a "
        "Streamlit render call at all."
    ),
    ("components.py", 329, " ($)"): (
        "Same as the eps_basic entry above -- _RAW_EXPORT_UNIT_SUFFIX['eps_diluted']."
    ),
    ("components.py", 334, " ($m)"): (
        "SPEC-008-batch-4 follow-up item 3: _RAW_EXPORT_DEFAULT_UNIT_SUFFIX, same CSV-only sink "
        "as the two entries above."
    ),
    ("format.py", 192, " ($m)"): (
        "SPEC-008 review D4: _LABEL_UNIT_SUFFIX['usd'], appended by format_metric_label. Every "
        "caller (metric_tile/metric_chart) passes the result to a widget label or axis title, "
        "neither of which is markdown-parsed the way st.markdown/write/caption are -- confirmed "
        "against the installed Streamlit version, not assumed."
    ),
    ("format.py", 59, "$"): (
        "format_usd_per_share's own f'${...}' prefix -- deliberately unescaped BY DESIGN, per "
        "that function's own docstring: its value either reaches st.metric (which does not "
        "markdown-parse at all) or is escaped by the CALLER at its own render call site "
        "(pages/metrics.py). Escaping belongs at the render call, not inside the formatter."
    ),
    ("format.py", 252, "$"): (
        "escape_markdown_currency's own text.replace('$', r'\\$') -- the search target of the "
        "escaping mechanism itself, not a value anything renders."
    ),
    ("format.py", 262, "^[A-Z][a-z]{2}\\.\\s\\d{1,2},\\s\\d{4}$"): (
        "_BARE_DATE_LINE: the trailing '$' is a regex end-of-string anchor (re.compile), not a "
        "currency symbol -- never reaches a render call."
    ),
    ("financials.py", 39, "** ($m)"): (
        "_render_summary's per-statement header (f'**{title}** ($m)') -- escaped via "
        "fmt.escape_markdown_currency before its one st.markdown call, a few lines below."
    ),
    ("financials.py", 106, "EPS in $ (2dp), shares and cash-flow figures in millions ($m), tax rate in %"): (
        "_KEY_METRICS_CAPTION -- escaped via fmt.escape_markdown_currency at its one render call "
        "site in _render_table (st.caption), not at the constant itself."
    ),
    ("financials.py", 138, "$m"): (
        "_render_table's caption ternary, other branch to _KEY_METRICS_CAPTION above -- same "
        "variable, same escape_markdown_currency call site."
    ),
    (
        "components.py", 678,
        "Model-ready: the same rows and columns as the table above, but as plain numbers -- "
        "no thousands separators, no $/%, negatives as '-' not parentheses, blank cells left "
        "genuinely empty (never '--' or 'n/m'). Units are stated per row in the line-item "
        "label, since this table mixes $m, $, share counts, and decimal fractions.",
    ): (
        "SPEC-008-batch-4 follow-up item 4: the raw-CSV download button's help= tooltip -- THE "
        "bug this guard was rewritten for. Escaped via fmt.escape_markdown_currency at its one "
        "call site, a few lines below where this string is defined."
    ),
}


def test_no_unescaped_currency_reaches_streamlit_markdown():
    """SPEC-008-batch-4 follow-up item 4 (approved 2026-08-20). Confirmed
    live, against the real bug, before this design existed: run against
    the pre-fix source (download_button's help= text with two bare '$'
    characters, batch 4 follow-up item 3's own CSV export), this test
    failed with `components.py:<line>: 'Model-ready: ... no $/%, ...'`
    listed among the offenders -- not assumed to work, verified red before
    it was made green. See the module comment above for why this guard no
    longer names any Streamlit function at all."""
    offenders = []
    for path in sorted(DASHBOARD_DIR.rglob("*.py")):
        for lineno, s in _dollar_string_literals(path):
            key = (path.name, lineno, s)
            if key not in _ALLOWED_DOLLAR_STRINGS:
                offenders.append(f"{path.name}:{lineno}: {s!r} -- not escaped and not on the allowlist")
    assert not offenders, "\n".join(offenders)


def test_dollar_allowlist_has_no_stale_entries():
    """The allowlist above is only meaningful if every entry still points
    at a real, current '$'-bearing string -- a stale entry (the literal it
    named was edited, moved, or deleted) would sit here forever, granting
    silent cover to nothing, while whatever replaced it would correctly
    fail the test above on its own. Keeps the allowlist honest as the
    codebase changes, the same way the test above keeps the codebase
    honest."""
    all_found = {
        (path.name, lineno, s)
        for path in sorted(DASHBOARD_DIR.rglob("*.py"))
        for lineno, s in _dollar_string_literals(path)
    }
    stale = sorted(set(_ALLOWED_DOLLAR_STRINGS) - all_found)
    assert not stale, f"allowlist entries with no matching string anymore: {stale}"
